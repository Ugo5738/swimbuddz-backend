"""Private transfer instructions and uniform, auditable offline settlement."""

import hashlib
import hmac
from datetime import timedelta
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text

from libs.common.config import get_settings
from libs.common.currency import naira_to_kobo
from libs.common.datetime_utils import utc_now
from libs.common.service_client import internal_get
from services.payments_service.models import Payment, PaymentStatus


def transfer_token(reference: str) -> str:
    # A purpose-specific capability, not an authentication JWT. The fragment
    # never goes to a web server/referer; callers send it in the POST body.
    return hmac.new(
        get_settings().SUPABASE_JWT_SECRET.encode(),
        f"payment-transfer:v1:{reference}".encode(),
        hashlib.sha256,
    ).hexdigest()


def transfer_checkout_url(reference: str) -> str:
    return f"/payments/transfer/{quote(reference, safe='')}#token={transfer_token(reference)}"


def check_transfer_token(reference: str, token: str) -> None:
    if not hmac.compare_digest(transfer_token(reference).encode(), token.encode()):
        raise HTTPException(404, "Payment not found")


async def validate_receipt_media(media_id, payment, actor) -> None:
    if media_id is None:
        return
    response = await internal_get(
        service_url=get_settings().MEDIA_SERVICE_URL,
        path=f"/media/internal/payment-proof/{media_id}",
        calling_service="payments",
    )
    if response.status_code != 200:
        raise HTTPException(422, "Receipt file is unavailable")
    media = response.json()
    metadata = media.get("metadata") or media.get("metadata_info") or {}
    if metadata.get("purpose") != "payment_proof" or str(
        media.get("uploaded_by")
    ) not in {str(actor.user_id), payment.member_auth_id}:
        raise HTTPException(
            422, "Choose a private payment receipt uploaded by you or the payer"
        )


async def lock_external_reference(db, reference: str | None) -> None:
    if reference:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"offline-receipt:{reference.strip().lower()}"},
        )


async def settle_offline(db, payment, payload, actor):
    """Settle the frozen payable amount. Never synthesize an arbitrary entitlement."""
    from services.payments_service.routers.intents._entitlement import (
        _mark_paid_and_apply,
    )

    await lock_external_reference(db, payload.external_reference)
    payment = (
        await db.execute(
            select(Payment).where(Payment.id == payment.id).with_for_update()
        )
    ).scalar_one()
    if naira_to_kobo(payment.amount) != payload.amount_kobo or payload.amount_kobo <= 0:
        raise HTTPException(
            422, "The received allocation must equal the full frozen payment amount"
        )
    if payment.status == PaymentStatus.PAID:
        if (
            payment.provider == "offline"
            and payment.provider_reference == payload.external_reference
            and payment.payment_method == payload.payment_method
        ):
            return payment
        raise HTTPException(409, "This payment is already paid; do not record it again")
    if payment.status not in {PaymentStatus.PENDING, PaymentStatus.PENDING_REVIEW}:
        raise HTTPException(
            409,
            "This checkout is closed; prepare a current quote before recording payment",
        )
    if (payment.payment_metadata or {}).get("bubbles_to_apply") or (
        payment.payment_metadata or {}
    ).get("wallet_hold_id"):
        raise HTTPException(
            409, "A mixed Bubbles checkout cannot be settled as an offline payment"
        )
    if (
        payload.received_at.tzinfo is None
        or payload.received_at > utc_now() + timedelta(minutes=5)
    ):
        raise HTTPException(
            422, "Received time must include a timezone and cannot be in the future"
        )
    if payload.external_reference:
        duplicate = (
            await db.execute(
                select(Payment)
                .where(
                    Payment.id != payment.id,
                    Payment.status == PaymentStatus.PAID,
                    func.lower(Payment.provider_reference)
                    == payload.external_reference.lower(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if duplicate:
            raise HTTPException(
                409,
                "This bank/receipt reference is already recorded; review its allocation",
            )
    booking_id = payment.session_booking_id or (payment.payment_metadata or {}).get(
        "booking_id"
    )
    if booking_id:
        await lock_external_reference(db, f"booking:{booking_id}")
        duplicate = (
            await db.execute(
                select(Payment)
                .where(
                    Payment.id != payment.id,
                    Payment.status == PaymentStatus.PAID,
                    or_(
                        Payment.session_booking_id == booking_id,
                        Payment.payment_metadata["booking_id"].astext
                        == str(booking_id),
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if duplicate:
            raise HTTPException(409, "This booking already has a paid payment")
    await validate_receipt_media(payload.proof_media_id, payment, actor)
    payment.payment_metadata = {
        **(payment.payment_metadata or {}),
        "recorded_offline": True,
        "recorded_by_auth_id": actor.user_id,
        "external_reference": payload.external_reference,
        "previous_payment_method": payment.payment_method,
        "offline_recorded_at": utc_now().isoformat(),
    }
    payment.payment_method = payload.payment_method
    payment.admin_review_note = payload.note
    if payload.proof_media_id:
        payment.proof_of_payment_media_id = payload.proof_media_id
    return await _mark_paid_and_apply(
        db,
        payment,
        provider="offline",
        provider_reference=payload.external_reference,
        paid_at=payload.received_at,
        provider_payload={
            "recorded_by_auth_id": actor.user_id,
            "payment_method": payload.payment_method,
        },
    )
