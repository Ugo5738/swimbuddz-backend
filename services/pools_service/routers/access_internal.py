"""Pool Access reservation quote and activation, service-to-service only."""

import uuid
import httpx
from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.pools_service.models.access import PoolAccessBooking, PoolAccessOffer

router = APIRouter(tags=["internal-pool-access"])


@router.get("/bookings/{booking_id}/quote")
async def quote(
    booking_id: uuid.UUID,
    member_auth_id: str,
    _user=Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    booking = await db.get(PoolAccessBooking, booking_id)
    if not booking or booking.buyer_auth_id != member_auth_id:
        raise HTTPException(404, "Booking not found")
    if booking.status != "pending_payment" or booking.hold_expires_at <= utc_now():
        raise HTTPException(409, "Booking hold has expired")
    offer = await db.get(PoolAccessOffer, booking.offer_id)
    if not offer or offer.starts_at <= utc_now():
        raise HTTPException(409, "Visit no longer available")
    if booking.currency != "NGN":
        raise HTTPException(422, "Only NGN checkout is currently supported")
    return {
        "booking_id": str(booking.id),
        "total_kobo": booking.selling_total_kobo,
        "currency": booking.currency,
        "hold_expires_at": booking.hold_expires_at.isoformat(),
    }


@router.post("/bookings/{booking_id}/claim-checkout")
async def claim_checkout(
    booking_id: uuid.UUID,
    payload: dict,
    _user=Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    reference = str(payload.get("payment_reference") or "")
    auth_id = str(payload.get("member_auth_id") or "")
    if not reference.startswith("PAY-") or not auth_id:
        raise HTTPException(422, "Invalid payment reference")
    booking = (
        await db.execute(
            select(PoolAccessBooking)
            .where(PoolAccessBooking.id == booking_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not booking or booking.buyer_auth_id != auth_id:
        raise HTTPException(404, "Booking not found")
    if booking.status != "pending_payment" or booking.hold_expires_at <= utc_now():
        raise HTTPException(409, "Booking hold is not active")
    if booking.checkout_reference and booking.checkout_reference != reference:
        raise HTTPException(409, "This booking already has a pending checkout")
    booking.checkout_reference = reference
    await db.commit()
    return {"reference": reference, "status": "claimed"}


@router.post("/bookings/{booking_id}/confirm")
async def confirm(
    booking_id: uuid.UUID,
    payload: dict,
    _user=Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    reference = str(payload.get("payment_reference") or "")
    member_auth_id = str(payload.get("member_auth_id") or "")
    amount_kobo = payload.get("amount_kobo")
    if (
        not reference.startswith("PAY-")
        or not member_auth_id
        or not isinstance(amount_kobo, int)
        or isinstance(amount_kobo, bool)
    ):
        raise HTTPException(422, "Missing verified payment context")
    # Service JWT proves the caller is internal, not that a payment occurred.
    # Read persisted PAID evidence from payments_service before granting access.
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            evidence = await client.get(
                f"{settings.PAYMENTS_SERVICE_URL}/internal/payments/pool-access/paid/{reference}",
                headers={"Authorization": f"Bearer {_service_role_jwt('pools')}"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            503, "Payment verification is temporarily unavailable"
        ) from exc
    if evidence.status_code >= 400:
        raise HTTPException(409, "Verified payment evidence not found")
    record = evidence.json()
    if (
        record.get("booking_id") != str(booking_id)
        or record.get("member_auth_id") != member_auth_id
        or record.get("amount_kobo") != amount_kobo
        or record.get("currency") != "NGN"
    ):
        raise HTTPException(409, "Paid payment does not match this booking")

    booking = (
        await db.execute(
            select(PoolAccessBooking)
            .where(PoolAccessBooking.id == booking_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not booking or booking.buyer_auth_id != member_auth_id:
        raise HTTPException(404, "Booking not found")
    if booking.status == "confirmed":
        if booking.payment_reference != reference:
            raise HTTPException(409, "Booking confirmed by another payment")
        return {"status": "confirmed", "booking_id": str(booking.id)}
    if booking.status != "pending_payment":
        raise HTTPException(409, "Booking unavailable")
    if booking.checkout_reference != reference:
        raise HTTPException(409, "Unexpected payment reference")
    if booking.selling_total_kobo != amount_kobo or booking.currency != "NGN":
        raise HTTPException(409, "Payment amount or currency mismatch")
    if booking.hold_expires_at <= utc_now():
        raise HTTPException(409, "Booking expired; requires refund review")
    booking.payment_reference = reference
    booking.status = "confirmed"
    booking.confirmed_at = utc_now()
    await db.commit()
    return {"status": "confirmed", "booking_id": str(booking.id)}
