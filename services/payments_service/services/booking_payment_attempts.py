"""Serialize payment creation and settlement by booking, including legacy rows.

A checkout key is only a retry key. The booking is the commercial identity.
Provider-exposed failures require fresh verification before replacement. Local
supersession preserves late receipts for reconciliation; it cannot revoke a
previously issued hosted checkout.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import or_, select

from libs.common.currency import naira_to_kobo
from libs.common.datetime_utils import utc_now
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.payments_service.services.manual_transfer import lock_external_reference


def booking_identity(payment) -> uuid.UUID | None:
    if payment.purpose != PaymentPurpose.SESSION_BOOKING:
        return None
    raw = getattr(payment, "session_booking_id", None) or (
        payment.payment_metadata or {}
    ).get("booking_id")
    return uuid.UUID(str(raw)) if raw else None


async def lock_booking_payment(db, booking_id: uuid.UUID) -> None:
    # Same namespace as the existing Admin offline-receipt path.
    await lock_external_reference(db, f"booking:{booking_id}")


async def booking_payments(db, booking_id: uuid.UUID) -> list[Payment]:
    return list(
        (
            await db.execute(
                select(Payment)
                .where(
                    Payment.purpose == PaymentPurpose.SESSION_BOOKING,
                    or_(
                        Payment.session_booking_id == booking_id,
                        Payment.payment_metadata["booking_id"].astext
                        == str(booking_id),
                    ),
                )
                .order_by(Payment.created_at, Payment.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


def provider_exposed(payment: Payment) -> bool:
    meta = payment.payment_metadata or {}
    return bool(
        payment.provider == "paystack"
        or payment.provider_reference
        or meta.get("paystack")
        or meta.get("provider_initialization_started_at")
    )


def blocks_new_attempt(payment: Payment) -> bool:
    meta = payment.payment_metadata or {}
    if payment.status == PaymentStatus.FAILED and (
        meta.get("checkout_closed_unpaid") or meta.get("booking_attempt_superseded")
    ):
        return False
    return payment.status in {
        PaymentStatus.PENDING,
        PaymentStatus.PENDING_REVIEW,
        PaymentStatus.PAID,
        PaymentStatus.WAIVED,
    } or provider_exposed(payment)


async def existing_booking_attempt(
    db, booking_id, member_auth_id, payload, retry=None, replacement_reference=None
):
    rows = await booking_payments(db, booking_id)
    relevant = [row for row in rows if blocks_new_attempt(row)]
    if any(row.member_auth_id != member_auth_id for row in relevant):
        raise HTTPException(409, "Booking payment ownership needs Admin reconciliation")
    paid = next(
        (
            row
            for row in relevant
            if row.status in {PaymentStatus.PAID, PaymentStatus.WAIVED}
        ),
        None,
    )
    if paid:
        if retry is not None and retry.id == paid.id:
            return paid  # A lost response returns the receipt, never a new payment.
        raise HTTPException(
            409,
            "This booking is already paid; any pending booking update will be retried. Do not pay again.",
        )
    if len(relevant) > 1:
        raise HTTPException(
            409,
            "This booking has multiple historical payment links. Admin must reconcile them before another payment can start.",
        )
    if not relevant:
        return None
    payment = relevant[0]
    if payment.status not in {PaymentStatus.PENDING, PaymentStatus.PENDING_REVIEW}:
        if replacement_reference:
            from services.payments_service.services.booking_checkout_retry import (
                supersede_unpaid_booking_checkout,
            )

            if await supersede_unpaid_booking_checkout(
                db, payment, replacement_reference
            ):
                return None
        raise HTTPException(
            409,
            "An earlier provider payment needs reconciliation before another attempt can start",
        )
    meta = payment.payment_metadata or {}
    if meta.get("request_fingerprint"):
        from services.payments_service.services.product_intent_retry import (
            request_fingerprint,
        )

        matches = meta["request_fingerprint"] == request_fingerprint(payload)
    else:
        # Legacy Admin links have no request fingerprint. Resume only their
        # frozen amount/method; never disguise a cash link as Bubbles checkout.
        matches = (
            payload.payment_method == (payment.payment_method or "paystack")
            and (payload.bubbles_to_apply or 0)
            == int(meta.get("bubbles_to_apply") or 0)
            and not payload.discount_code
            and not payload.ride_config_id
            and not payload.pickup_location_id
            and (
                payload.session_id is None
                or str(payload.session_id) == str(meta.get("session_id"))
            )
            and (
                payload.direct_amount is None
                or naira_to_kobo(payload.direct_amount)
                == naira_to_kobo(payment.amount)
                + int(meta.get("bubbles_value_ngn") or 0) * 100
            )
        )
    if not matches:
        raise HTTPException(
            409,
            "A payment is already open for this booking. Resume its original payment options from Billing, or ask Admin to reconcile it before changing them.",
        )
    return payment


async def duplicate_paid_booking(db, payment):
    booking_id = booking_identity(payment)
    if booking_id is None:
        return None
    await lock_booking_payment(db, booking_id)
    return next(
        (
            row
            for row in await booking_payments(db, booking_id)
            if row.id != payment.id
            and row.status in {PaymentStatus.PAID, PaymentStatus.WAIVED}
            and not (row.payment_metadata or {}).get("booking_reconciliation")
            and not (row.payment_metadata or {}).get("checkout_reconciliation")
        ),
        None,
    )


def flag_duplicate_receipt(payment, settled):
    payment.entitlement_error = (
        "Duplicate booking payment received; Admin reconciliation/refund required"
    )
    payment.payment_metadata = {
        **(payment.payment_metadata or {}),
        "booking_reconciliation": {
            "reason": "another_payment_already_settled_booking",
            "settled_payment_id": str(settled.id),
            "settled_reference": settled.reference,
        },
    }


async def guard_booking_fulfillment(payment):
    """Also guard pre-existing paid rows replayed directly by workers/Admin."""
    from sqlalchemy.ext.asyncio import async_object_session

    booking_id = booking_identity(payment)
    if booking_id is None:
        return
    db = async_object_session(payment)
    if not booking_id or db is None:
        return
    await lock_booking_payment(db, booking_id)
    await db.refresh(payment)
    paid = [
        row
        for row in await booking_payments(db, booking_id)
        if row.status in {PaymentStatus.PAID, PaymentStatus.WAIVED}
        and not (row.payment_metadata or {}).get("booking_reconciliation")
        and not (row.payment_metadata or {}).get("checkout_reconciliation")
    ]
    # Preserve an existing fulfilled receipt; otherwise the first recorded
    # receipt owns fulfillment, even while its downstream retry is pending.
    paid.sort(
        key=lambda row: (
            row.entitlement_applied_at is None,
            row.paid_at or row.created_at,
            str(row.id),
        )
    )
    if paid and paid[0].id != payment.id:
        flag_duplicate_receipt(payment, paid[0])


async def initialize_booking_checkout(db, payment, email, redirect, initialize):
    """Only one request may cross the provider initialization boundary.

    The durable marker is written before the HTTP request. If its outcome is
    unknown, retain the same payment for provider reconciliation, never create
    another reference or assume that retrying initialization revokes the first.
    """
    await lock_booking_payment(db, booking_identity(payment))
    await db.refresh(payment)
    meta = payment.payment_metadata or {}
    existing = meta.get("paystack") or {}
    if payment.status in {PaymentStatus.PAID, PaymentStatus.WAIVED}:
        return None, None
    if existing.get("authorization_url"):
        return existing["authorization_url"], existing.get("access_code")
    if payment.status != PaymentStatus.PENDING:
        raise HTTPException(
            409, "This payment has changed; refresh Billing before continuing"
        )
    if meta.get("provider_initialization_started_at"):
        raise HTTPException(
            503,
            "This payment is still being prepared or needs provider reconciliation. Do not start another payment; refresh Billing shortly.",
        )
    if not email:
        raise HTTPException(400, "An email address is required for online payment")
    payment.payment_metadata = {
        **meta,
        "provider_initialization_started_at": utc_now().isoformat(),
    }
    payment.provider, payment.provider_reference = "paystack", payment.reference
    await db.commit()
    url, code = await initialize(payment, email, redirect)
    await lock_booking_payment(db, booking_identity(payment))
    await db.refresh(payment)
    payment.provider, payment.provider_reference = "paystack", payment.reference
    payment.payment_metadata = {
        **payment.payment_metadata,
        "paystack": {"authorization_url": url, "access_code": code},
    }
    await db.commit()
    return (url, code) if payment.status == PaymentStatus.PENDING else (None, None)
