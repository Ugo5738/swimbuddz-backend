"""Review historical checkout attempts without altering financial history."""

import hashlib
import json
import uuid
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from libs.auth.dependencies import require_admin
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import internal_post
from libs.db.session import get_async_db
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.payments_service.services.booking_payment_attempts import (
    booking_identity,
    booking_payments,
    lock_booking_payment,
    provider_exposed,
)

router = APIRouter(
    prefix="/admin/checkout-reconciliation", dependencies=[Depends(require_admin)]
)


def _timestamp(value):
    # SQLAlchemy's Python default may be naive until the first reload, whereas
    # PostgreSQL returns an aware timestamp. Both must produce the same digest.
    if value is None:
        return None
    return (
        value.replace(tzinfo=value.tzinfo or timezone.utc)
        .astimezone(timezone.utc)
        .isoformat()
    )


def snapshot(payment):
    return {
        "reference": payment.reference,
        "purpose": payment.purpose.value,
        "status": payment.status.value,
        "amount": float(payment.amount),
        "currency": payment.currency,
        "provider_exposed": provider_exposed(payment),
        "entitlement_applied_at": _timestamp(payment.entitlement_applied_at),
        "entitlement_error": payment.entitlement_error,
        "metadata": payment.payment_metadata or {},
        "updated_at": _timestamp(payment.updated_at),
    }


def digest(rows):
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str).encode()
    ).hexdigest()


@router.get("/bookings/{booking_id}")
async def preview_booking_attempts(booking_id: uuid.UUID, db=Depends(get_async_db)):
    rows = [snapshot(row) for row in await booking_payments(db, booking_id)]
    return {
        "booking_id": booking_id,
        "payments": rows,
        "preview_token": digest(rows),
        "applied": False,
    }


@router.get("/{reference}")
async def preview_checkout(reference: str, db=Depends(get_async_db)):
    payment = await db.scalar(select(Payment).where(Payment.reference == reference))
    if not payment:
        raise HTTPException(404, "Payment not found")
    row = snapshot(payment)
    return {
        "payment": row,
        "preview_token": digest(row),
        "applied": False,
        "release_policy": "Provider-exposed holds remain protected until payment or documented provider closure. A local timeout or abandoned status is not closure evidence.",
    }


class CloseUnpaidCheckout(BaseModel):
    preview_token: str
    provider_closure_evidence: str = Field(min_length=10, max_length=500)
    note: str = Field(min_length=10, max_length=1000)
    apply: bool = False


@router.post("/{reference}/close-unpaid")
async def close_unpaid_checkout(
    reference: str,
    body: CloseUnpaidCheckout,
    actor=Depends(require_admin),
    db=Depends(get_async_db),
):
    """Audited manual closure only after an operator verifies non-payability.

    This endpoint does not cancel Paystack. It records evidence that an
    operator obtained from the provider (or verified for a manual transfer).
    It never marks a payment paid, refunds money, or changes a paid receipt.
    """
    payment = await db.scalar(select(Payment).where(Payment.reference == reference))
    if not payment or payment.purpose not in {
        PaymentPurpose.CLUB,
        PaymentPurpose.SESSION_BOOKING,
        PaymentPurpose.ACADEMY_COHORT,
    }:
        raise HTTPException(404, "Supported checkout not found")
    booking_id = booking_identity(payment)
    if booking_id:
        await lock_booking_payment(db, booking_id)
    await db.refresh(payment, with_for_update=True)
    if (
        payment.status in {PaymentStatus.PAID, PaymentStatus.WAIVED}
        or payment.entitlement_applied_at
    ):
        raise HTTPException(
            409, "A paid or fulfilled checkout cannot be closed as unpaid"
        )
    if payment.purpose == PaymentPurpose.ACADEMY_COHORT and (
        payment.proof_of_payment_media_id is not None
        or payment.status == PaymentStatus.PENDING_REVIEW
        or (payment.payment_metadata or {}).get("recorded_offline")
    ):
        raise HTTPException(
            409,
            "Academy proof or offline receipt exists. Verify and reconcile it; do not close as unpaid",
        )
    meta = payment.payment_metadata or {}
    closed = meta.get("checkout_closed_unpaid")
    replay = closed and closed.get("preview_token") == body.preview_token
    if not replay and digest(snapshot(payment)) != body.preview_token:
        raise HTTPException(409, "Checkout changed; preview again before closing it")
    if not body.apply:
        return {
            "applied": False,
            "reference": reference,
            "action": "Record provider closure and release unpaid holds",
            "preview_token": body.preview_token,
        }
    if not replay:
        closed = {
            "actor": actor.user_id,
            "at": utc_now().isoformat(),
            "preview_token": body.preview_token,
            "provider_closure_evidence": body.provider_closure_evidence,
            "note": body.note,
        }
        payment.payment_metadata = {**meta, "checkout_closed_unpaid": closed}
        payment.status = PaymentStatus.FAILED
        await db.commit()
    # Commit the audit before releasing capacity. A late receipt is then
    # retained for reconciliation rather than reactivating a closed checkout.
    application_id = meta.get("club_application_id")
    if payment.purpose == PaymentPurpose.CLUB and application_id:
        response = await internal_post(
            service_url=get_settings().MEMBERS_SERVICE_URL,
            path=f"/clubs/internal/applications/{application_id}/reservation/release",
            calling_service="payments",
            json={
                "payment_reference": reference,
                "closure_evidence": closed["provider_closure_evidence"],
            },
            timeout=90,
        )
        if response.status_code >= 400:
            raise HTTPException(
                502,
                "Closure is recorded; retry this same request to finish releasing Club capacity",
            )
    from services.payments_service.routers.intents._helpers import _release_bubbles_hold

    await _release_bubbles_hold(payment)
    return {"applied": True, "reference": reference, "closed_unpaid": True}
