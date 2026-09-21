"""Named Experience orders use the same product pricing and wallet settlement.

Only Members calls this service-owned path, after checking the order token and
authenticating a wallet's owner. The order reference freezes payment choices.
"""

import uuid

import httpx

from fastapi import HTTPException
from sqlalchemy import select, text

from libs.common.currency import naira_to_kobo
from libs.common.datetime_utils import utc_now
from libs.common.service_client import create_wallet_hold, release_wallet_hold
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.payments_service.schemas import InternalInitializeResponse
from services.payments_service.services.checkout_pricing import (
    price_product_checkout,
    product_components,
    verify_expected_total,
)


def frozen_quote(payment, req):
    meta = payment.payment_metadata or {}
    quote = meta.get("checkout_quote")
    if not quote and meta.get("experience_order_amount_kobo") is not None:
        # Pending cash orders created before product modifiers were introduced
        # remain resumable at their original price and processing charges.
        subtotal = int(meta["experience_order_amount_kobo"])
        fees = int(meta.get("additional_charges_total_kobo") or 0)
        if naira_to_kobo(payment.amount) != subtotal + fees:
            raise HTTPException(
                409, "The original Experience payment needs reconciliation"
            )
        quote = {
            "subtotal_kobo": subtotal,
            "discount_code": None,
            "discount_kobo": 0,
            "discount_allocations_kobo": {},
            "net_subtotal_kobo": subtotal,
            "net_components_kobo": product_components(
                PaymentPurpose.COMMUNITY_EXPERIENCE, subtotal, req.metadata
            ),
            "bubbles_to_apply": 0,
            "bubbles_value_kobo": 0,
            "maximum_bubbles": 0,
            "cash_subtotal_kobo": subtotal,
            "additional_charges": meta.get("additional_charges") or [],
            "additional_charges_total_kobo": fees,
            "total_kobo": subtotal + fees,
        }
    if (
        not quote
        or payment.member_auth_id != req.member_auth_id
        or payment.currency != req.currency
        or payment.payment_method != req.payment_method
        or meta.get("experience_order_id")
        != (req.metadata or {}).get("experience_order_id")
    ):
        raise HTTPException(409, "Payment reference belongs to a different checkout")
    if (
        quote["discount_code"]
        != (req.discount_code.strip().upper() if req.discount_code else None)
        or quote["bubbles_to_apply"] != req.bubbles_to_apply
    ):
        raise HTTPException(
            409,
            "This checkout has already started. Resume its original discount and Bubbles selection.",
        )
    return {**quote, "selection_locked": True}


async def preview_experience_checkout(req, db, *, consume_discount=False):
    if req.purpose != "community_experience" or not (req.metadata or {}).get(
        "experience_order_id"
    ):
        raise HTTPException(422, "A server-owned Experience order is required")
    if req.bubbles_to_apply and req.member_auth_id.startswith("experience-guest:"):
        raise HTTPException(403, "Sign in to use your Bubbles")
    if not consume_discount:
        payment = (
            await db.execute(select(Payment).where(Payment.reference == req.reference))
        ).scalar_one_or_none()
        if payment:
            return frozen_quote(payment, req)
    return await price_product_checkout(
        db,
        purpose=PaymentPurpose.COMMUNITY_EXPERIENCE,
        currency=req.currency,
        components=product_components(
            PaymentPurpose.COMMUNITY_EXPERIENCE, naira_to_kobo(req.amount), req.metadata
        ),
        discount_code=req.discount_code,
        payment_method=req.payment_method,
        bubbles_to_apply=req.bubbles_to_apply,
        consume_discount=consume_discount,
    )


async def initialize_experience_checkout(req, db):
    from services.payments_service.routers.intents._entitlement import (
        _mark_paid_and_apply,
    )
    from services.payments_service.routers.intents._paystack import (
        _initialize_paystack,
        _paystack_enabled,
    )

    # The order predates the Payment row. Serialize even the first concurrent
    # initializer so a losing request cannot release the winner's wallet hold.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:reference, 0))"),
        {"reference": req.reference},
    )
    payment = (
        await db.execute(
            select(Payment).where(Payment.reference == req.reference).with_for_update()
        )
    ).scalar_one_or_none()
    if payment:
        meta = payment.payment_metadata or {}
        quote = frozen_quote(payment, req)
        if not meta.get("checkout_quote"):
            payment.payment_metadata = meta = {**meta, "checkout_quote": quote}
            await db.commit()
        if payment.status == PaymentStatus.PAID:
            # The fulfillment retry worker owns failed activation; never charge again.
            return InternalInitializeResponse(
                reference=payment.reference,
                amount_kobo=naira_to_kobo(payment.amount),
                confirmed=bool(payment.entitlement_applied_at),
                checkout_quote=quote,
            )
        if payment.status not in {PaymentStatus.PENDING, PaymentStatus.PENDING_REVIEW}:
            raise HTTPException(
                409,
                "This payment is closed. Check its status before starting a new order.",
            )
        if meta.get("internal_checkout"):
            return InternalInitializeResponse(
                **{**meta["internal_checkout"], "checkout_quote": quote}
            )
    else:
        quote = await preview_experience_checkout(req, db, consume_discount=True)
        verify_expected_total(quote, req.expected_total_kobo)
        if (
            quote["total_kobo"]
            and req.payment_method == "paystack"
            and not _paystack_enabled()
        ):
            raise HTTPException(503, "Online payment is currently unavailable")
        payment_id = uuid.uuid4()
        hold_id = None
        if req.bubbles_to_apply:
            try:
                hold = await create_wallet_hold(
                    req.member_auth_id,
                    amount=req.bubbles_to_apply,
                    idempotency_key=f"payment-intent:{payment_id}:bubbles",
                    description=f"Community Experience {req.reference}",
                    calling_service="payments",
                    reference_type="community_experience",
                    reference_id=str(payment_id),
                    expires_in_seconds=1800,
                )
            except httpx.HTTPStatusError as exc:
                from services.payments_service.routers.intents.intent_creation import (
                    _service_error_detail,
                )

                raise HTTPException(
                    exc.response.status_code
                    if exc.response.status_code in {400, 402, 403, 404, 409}
                    else 503,
                    _service_error_detail(
                        exc.response, "Could not reserve the selected Bubbles"
                    ),
                ) from exc
            hold_id = str(hold["id"])
        payment = Payment(
            id=payment_id,
            reference=req.reference,
            member_auth_id=req.member_auth_id,
            payer_email=(req.metadata or {}).get("payer_email"),
            purpose=PaymentPurpose.COMMUNITY_EXPERIENCE,
            amount=quote["total_kobo"] / 100,
            currency=req.currency,
            status=PaymentStatus.PENDING,
            payment_method=req.payment_method,
            payment_metadata={
                **req.metadata,
                "checkout_quote": quote,
                "bubbles_to_apply": req.bubbles_to_apply,
                "bubbles_value_ngn": quote["bubbles_value_kobo"] / 100,
                "wallet_hold_id": hold_id,
                "discount_code": quote["discount_code"],
                "discount_applied": quote["discount_kobo"] / 100,
                "additional_charges": quote["additional_charges"],
                "additional_charges_total_kobo": quote["additional_charges_total_kobo"],
            },
            admin_payment_notification_required=bool(
                req.bubbles_to_apply and not quote["total_kobo"]
            ),
        )
        db.add(payment)
        try:
            await db.commit()
            await db.refresh(payment)
        except Exception:
            await db.rollback()
            if hold_id:
                await release_wallet_hold(hold_id, calling_service="payments")
            raise

    if payment.amount == 0:
        payment = await _mark_paid_and_apply(
            db=db,
            payment=payment,
            provider="internal",
            provider_reference=f"checkout:{req.reference}",
            paid_at=utc_now(),
        )
        return InternalInitializeResponse(
            reference=req.reference,
            amount_kobo=0,
            confirmed=bool(payment.entitlement_applied_at),
            checkout_quote=quote,
        )
    if req.payment_method == "manual_transfer":
        from services.payments_service.services.manual_transfer import (
            transfer_checkout_url,
        )

        response = InternalInitializeResponse(
            reference=req.reference,
            authorization_url=transfer_checkout_url(req.reference),
            amount_kobo=quote["total_kobo"],
            additional_charges=quote["additional_charges"],
            checkout_quote=quote,
        )
        payment.payment_metadata = {
            **payment.payment_metadata,
            "internal_checkout": response.model_dump(),
        }
        await db.commit()
        return response
    # Persisted choices/hold survive an uncertain provider response. Retry this
    # reference; never reserve a second wallet debit or consume a second code use.
    authorization_url, access_code = await _initialize_paystack(
        payment, email=payment.payer_email, redirect_path=req.callback_url
    )
    response = InternalInitializeResponse(
        reference=req.reference,
        authorization_url=authorization_url,
        access_code=access_code,
        amount_kobo=quote["total_kobo"],
        additional_charges=quote["additional_charges"],
        checkout_quote=quote,
    )
    payment.payment_metadata = {
        **payment.payment_metadata,
        "internal_checkout": response.model_dump(),
    }
    await db.commit()
    return response
