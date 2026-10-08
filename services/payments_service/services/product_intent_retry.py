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
    if getattr(payload.purpose, "value", payload.purpose) == "session_booking":
        data["booking_id"] = str(
            (payload.payment_metadata or {}).get("booking_id") or ""
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
    visited = set()
    while payment and (payment.payment_metadata or {}).get(
        "booking_attempt_superseded"
    ):
        if payment.reference in visited or payment.member_auth_id != member_auth_id:
            raise HTTPException(409, "This payment chain needs Admin reconciliation")
        visited.add(payment.reference)
        replacement = payment.payment_metadata["booking_attempt_superseded"].get(
            "replacement_reference"
        )
        payment = (
            await db.execute(
                select(Payment)
                .where(Payment.reference == replacement)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if payment is None:
            raise HTTPException(
                409, "The replacement payment needs Admin reconciliation"
            )
        reference = payment.reference
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
    quote = meta.get("checkout_quote")
    if quote is not None:
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
    from services.payments_service.models import PaymentPurpose

    if (
        payment.purpose == PaymentPurpose.POOL_ACCESS
        and payment.status == PaymentStatus.PENDING
    ):
        import httpx
        from libs.auth.dependencies import _service_role_jwt
        from libs.common.config import get_settings

        booking_id = meta.get("pool_access_booking_id")
        if not booking_id:
            raise HTTPException(409, "Pool Access payment has no reservation")
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{settings.POOLS_SERVICE_URL}/internal/pools/access/bookings/{booking_id}/quote",
                    params={"member_auth_id": payment.member_auth_id},
                    headers={
                        "Authorization": f"Bearer {_service_role_jwt('payments')}"
                    },
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                503, "Booking verification temporarily unavailable"
            ) from exc
        if response.status_code >= 400:
            raise HTTPException(
                409,
                "Pool Access hold expired or is unavailable; payment requires review",
            )
    checkout_url = (meta.get("paystack") or {}).get("authorization_url")
    from services.payments_service.services.club_checkout_capacity import (
        protect_club_checkout,
    )

    if payment.status != PaymentStatus.PAID and checkout_url:
        await protect_club_checkout(db, payment)
        meta = payment.payment_metadata or {}
    if payment.status == PaymentStatus.PENDING and payment.amount == 0:
        await protect_club_checkout(db, payment)
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
        if not payment.payer_email:
            raise HTTPException(400, "An email address is required for online payment")
        await protect_club_checkout(db, payment)
        redirect = (
            f"/account/academy/enrollment-success?enrollment_id={meta['enrollment_id']}"
            if meta.get("enrollment_id")
            else None
        )
        from services.payments_service.models import PaymentPurpose

        if payment.purpose == PaymentPurpose.POOL_ACCESS:
            redirect = "/pool-access/my-bookings"
        if payment.purpose == PaymentPurpose.SESSION_BOOKING:
            from services.payments_service.services.booking_payment_attempts import (
                initialize_booking_checkout,
            )

            checkout_url, code = await initialize_booking_checkout(
                db, payment, payment.payer_email, redirect, _initialize_paystack
            )
        else:
            checkout_url, code = await _initialize_paystack(
                payment, payment.payer_email, redirect
            )
            await db.refresh(payment, with_for_update=True)
        meta = payment.payment_metadata or {}
        if checkout_url and payment.purpose != PaymentPurpose.SESSION_BOOKING:
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

        await protect_club_checkout(db, payment)
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
        original_amount=quote["subtotal_kobo"] / 100 if quote else None,
        discount_code=quote["discount_code"] if quote else None,
        discount_applied=quote["discount_kobo"] / 100 if quote else 0,
        additional_charges=quote["additional_charges"] if quote else [],
        additional_charges_total=quote["additional_charges_total_kobo"] / 100
        if quote
        else 0,
        entitlement_applied_at=payment.entitlement_applied_at,
    )
