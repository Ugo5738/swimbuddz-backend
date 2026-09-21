"""Resume the same member product payment after a lost/uncertain response."""

import hashlib
import json
import uuid

from fastapi import HTTPException
from sqlalchemy import select, text

from libs.common.datetime_utils import utc_now
from services.payments_service.models import Payment, PaymentStatus
from services.payments_service.schemas import PaymentIntentResponse
from services.payments_service.services.checkout_pricing import verify_expected_total


def request_fingerprint(payload):
    data = payload.model_dump(
        mode="json",
        exclude={"idempotency_key", "expected_total_kobo", "payment_metadata"},
    )
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


async def find_product_retry(db, payload, member_auth_id):
    reference = f"PAY-{uuid.uuid5(uuid.NAMESPACE_URL, f'{member_auth_id}:{payload.idempotency_key}').hex}"
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:reference, 0))"),
        {"reference": reference},
    )
    payment = (
        await db.execute(
            select(Payment).where(Payment.reference == reference).with_for_update()
        )
    ).scalar_one_or_none()
    if payment and (
        payment.member_auth_id != member_auth_id
        or (payment.payment_metadata or {}).get("request_fingerprint")
        != request_fingerprint(payload)
    ):
        raise HTTPException(
            409, "This checkout key is already associated with a different selection"
        )
    return reference, payment


async def resume_product_payment(db, payment, payload):
    from services.payments_service.routers.intents._entitlement import (
        _mark_paid_and_apply,
    )
    from services.payments_service.routers.intents._paystack import (
        _initialize_paystack,
        _paystack_enabled,
    )

    meta = payment.payment_metadata or {}
    quote = meta["checkout_quote"]
    verify_expected_total(quote, payload.expected_total_kobo)
    if payment.status not in {
        PaymentStatus.PENDING,
        PaymentStatus.PENDING_REVIEW,
        PaymentStatus.PAID,
    }:
        raise HTTPException(
            409,
            "This payment is closed. Check Billing before starting another payment.",
        )
    checkout_url = (meta.get("paystack") or {}).get("authorization_url")
    if payment.status == PaymentStatus.PENDING and payment.amount == 0:
        payment = await _mark_paid_and_apply(
            db=db,
            payment=payment,
            provider="internal",
            provider_reference=f"checkout:{payment.reference}",
            paid_at=utc_now(),
        )
    elif (
        payment.status == PaymentStatus.PENDING
        and payment.payment_method == "paystack"
        and not checkout_url
    ):
        if not _paystack_enabled():
            raise HTTPException(
                503,
                "Online payment is temporarily unavailable; resume this checkout later",
            )
        redirect = (
            f"/account/academy/enrollment-success?enrollment_id={meta['enrollment_id']}"
            if meta.get("enrollment_id")
            else None
        )
        checkout_url, code = await _initialize_paystack(
            payment, payment.payer_email, redirect
        )
        payment.provider, payment.provider_reference = "paystack", payment.reference
        payment.payment_metadata = {
            **meta,
            "paystack": {"authorization_url": checkout_url, "access_code": code},
        }
        await db.commit()
    if (
        payment.payment_method == "manual_transfer"
        and payment.status != PaymentStatus.PAID
    ):
        from services.payments_service.services.manual_transfer import (
            transfer_checkout_url,
        )

        checkout_url = transfer_checkout_url(payment.reference)
    return PaymentIntentResponse(
        reference=payment.reference,
        amount=payment.amount,
        currency=payment.currency,
        purpose=payment.purpose,
        status=payment.status,
        checkout_url=checkout_url if payment.status != PaymentStatus.PAID else None,
        created_at=payment.created_at,
        checkout_quote=quote,
        original_amount=quote["subtotal_kobo"] / 100,
        discount_code=quote["discount_code"],
        discount_applied=quote["discount_kobo"] / 100,
        additional_charges=quote["additional_charges"],
        additional_charges_total=quote["additional_charges_total_kobo"] / 100,
        entitlement_applied_at=payment.entitlement_applied_at,
    )
