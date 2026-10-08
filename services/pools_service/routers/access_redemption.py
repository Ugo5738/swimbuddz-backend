"""Authenticated reservation details and reception admission verification."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.pools_service.models.access import PoolAccessAdmission, PoolAccessBooking, PoolAccessOffer, PoolAccessReconciliation
from services.pools_service.services.access_policy import admission_id_from_ticket, reconciled_cost, ticket_for

router = APIRouter(tags=["pool-access-redemption"])


@router.get("/bookings/{booking_id}/tickets")
async def my_tickets(booking_id: uuid.UUID, user: AuthUser=Depends(get_current_user),
                     db: AsyncSession=Depends(get_async_db)):
    booking = await db.get(PoolAccessBooking, booking_id)
    if not booking or booking.buyer_auth_id != user.user_id:
        raise HTTPException(404, "Booking not found")
    if booking.status != "confirmed":
        raise HTTPException(409, "Tickets require confirmed payment")
    offer = await db.get(PoolAccessOffer, booking.offer_id)
    if offer.ends_at <= utc_now():
        raise HTTPException(409, "Admission window has ended")
    admissions = (await db.execute(select(PoolAccessAdmission).where(
        PoolAccessAdmission.booking_id == booking_id).order_by(PoolAccessAdmission.ordinal))).scalars().all()
    return [{"id": str(admission.id), "guest_name": admission.guest_name,
             "ticket": ticket_for(admission.id) if admission.checked_in_at is None else None,
             "checked_in_at": admission.checked_in_at} for admission in admissions]


@router.post("/redeem")
async def redeem(ticket: str, user: AuthUser=Depends(require_admin),
                 db: AsyncSession=Depends(get_async_db)):
    """Admin-only check-in for first pilot. Partner-scoped roles required before rollout."""
    admission_id = admission_id_from_ticket(ticket)
    admission = (await db.execute(select(PoolAccessAdmission).where(
        PoolAccessAdmission.id == admission_id).with_for_update())).scalar_one_or_none()
    if not admission:
        raise HTTPException(404, "Admission not found")
    booking = await db.get(PoolAccessBooking, admission.booking_id)
    offer = await db.get(PoolAccessOffer, booking.offer_id)
    now = utc_now()
    if booking.status != "confirmed" or not booking.payment_reference:
        raise HTTPException(409, "Booking is not paid")
    if now < offer.starts_at or now > offer.ends_at:
        raise HTTPException(409, "Outside permitted admission window")
    if admission.checked_in_at:
        raise HTTPException(409, "Ticket already redeemed")
    admission.checked_in_at = now
    admission.checked_in_by = user.user_id
    await db.commit()
    return {"status": "admitted", "admission_id": str(admission.id),
            "guest_name": admission.guest_name, "pool_id": str(offer.pool_id)}


@router.post("/bookings/{booking_id}/reconcile")
async def reconcile(booking_id: uuid.UUID, user: AuthUser=Depends(require_admin),
                    db: AsyncSession=Depends(get_async_db)):
    booking = (await db.execute(select(PoolAccessBooking).where(
        PoolAccessBooking.id == booking_id).with_for_update())).scalar_one_or_none()
    if not booking or booking.status != "confirmed" or not booking.payment_reference:
        raise HTTPException(409, "Only paid bookings can be reconciled")
    prior = (await db.execute(select(PoolAccessReconciliation).where(
        PoolAccessReconciliation.booking_id == booking_id))).scalar_one_or_none()
    if prior:
        return {"booking_id": str(booking_id), "verified_admissions": prior.verified_admissions,
                "payable_kobo": prior.payable_kobo, "status": "snapshotted"}
    offer = await db.get(PoolAccessOffer, booking.offer_id)
    if offer.ends_at > utc_now():
        raise HTTPException(409, "Visit has not finished")
    entries = (await db.execute(select(PoolAccessAdmission).where(
        PoolAccessAdmission.booking_id == booking_id,
        PoolAccessAdmission.checked_in_at.is_not(None)))).scalars().all()
    liability = reconciled_cost(booking.negotiated_cost_kobo, booking.cost_basis, len(entries))
    statement = PoolAccessReconciliation(
        booking_id=booking.id, verified_admissions=len(entries), payable_kobo=liability,
        currency=booking.currency, payment_reference=booking.payment_reference,
        reconciled_by=user.user_id,
    )
    db.add(statement)
    await db.commit()
    return {"booking_id": str(booking_id), "verified_admissions": len(entries),
            "payable_kobo": liability, "status": "snapshotted"}
