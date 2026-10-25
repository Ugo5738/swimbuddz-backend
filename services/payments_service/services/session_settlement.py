"""Frozen combined receipts; one cash-in, explicit charges, normal retry worker."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.currency import naira_to_kobo
from libs.common.datetime_utils import utc_now
from libs.common.service_client import internal_post
from services.payments_service.models import Payment, PaymentStatus
from services.payments_service.services.booking_payment_attempts import (
    lock_booking_payment,
)
from services.payments_service.services.manual_transfer import lock_external_reference

METADATA_KEY = "admin_session_settlement"


async def lock_subjects(
    db: AsyncSession, booking_ids: Sequence[UUID | str], guest_ids: Sequence[UUID | str]
) -> None:
    for booking_id in sorted(booking_ids, key=str):
        await lock_booking_payment(db, booking_id)
    for guest_id in sorted(guest_ids, key=str):
        await lock_external_reference(db, f"session-participant:{guest_id}")


async def sessions_request(action: str, body: dict[str, Any]) -> dict:
    response = await internal_post(
        service_url=get_settings().SESSIONS_SERVICE_URL,
        path=f"/internal/sessions/settlements/{action}",
        calling_service="payments",
        json=body,
    )
    if response.status_code >= 400:
        detail = "Session settlement could not be completed"
        try:
            detail = response.json().get("detail") or detail
        except ValueError:
            pass
        raise HTTPException(
            response.status_code if response.status_code < 500 else 502, detail
        )
    return response.json()


async def initialize_checkout(
    db: AsyncSession,
    payment: Payment,
    initialize: Callable[..., Awaitable[tuple[str | None, str | None]]],
) -> str | None:
    meta = payment.payment_metadata
    await lock_subjects(
        db, meta["booking_ids"], meta[METADATA_KEY]["guest_participant_ids"]
    )
    await db.refresh(payment)
    meta = payment.payment_metadata
    if payment.status == PaymentStatus.PAID:
        return None
    if payment.status != PaymentStatus.PENDING:
        raise HTTPException(
            409, "This combined payment needs reconciliation before continuing"
        )
    if (meta.get("paystack") or {}).get("authorization_url"):
        return meta["paystack"]["authorization_url"]
    if meta.get("provider_initialization_started_at"):
        raise HTTPException(
            503,
            "This combined payment is still being prepared; do not create another charge",
        )
    payment.payment_metadata = {
        **meta,
        "provider_initialization_started_at": utc_now().isoformat(),
    }
    payment.provider = "paystack"
    payment.provider_reference = payment.reference
    await db.commit()
    url, code = await initialize(payment, payment.payer_email)
    if not url:
        raise HTTPException(502, "Online payment could not be initialized")
    await lock_subjects(
        db, meta["booking_ids"], meta[METADATA_KEY]["guest_participant_ids"]
    )
    await db.refresh(payment)
    payment.payment_metadata = {
        **payment.payment_metadata,
        "paystack": {"authorization_url": url, "access_code": code},
    }
    await db.commit()
    return url if payment.status == PaymentStatus.PENDING else None


async def apply_session_settlement(payment: Payment) -> None:
    meta = payment.payment_metadata or {}
    settlement = meta[METADATA_KEY]
    if payment.status != PaymentStatus.PAID or payment.currency != "NGN":
        raise HTTPException(
            409, "Only verified NGN receipts can settle session charges"
        )
    if (
        naira_to_kobo(payment.amount) != settlement["total_kobo"]
        or sum(line["amount_kobo"] for line in settlement["lines"])
        != settlement["total_kobo"]
    ):
        raise HTTPException(
            409, "Combined receipt allocation does not match the collected amount"
        )
    await sessions_request(
        "confirm",
        {
            "member_auth_id": payment.member_auth_id,
            "booking_ids": meta["booking_ids"],
            "guest_participant_ids": settlement["guest_participant_ids"],
            "payment_intent_id": str(payment.id),
            "payment_reference": payment.reference,
            "paid_at": payment.paid_at.isoformat(),
            "total_kobo": settlement["total_kobo"],
            "lines": settlement["lines"],
        },
    )
