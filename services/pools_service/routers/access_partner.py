"""Restricted partner reception redemption for one authorised pool."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.pools_service.models.access import (
    PoolAccessAdmission, PoolAccessBooking, PoolAccessOffer, PoolAccessPartnerOperator,
)
from services.pools_service.services.access_policy import admission_id_from_ticket

router = APIRouter(tags=["pool-access-partners"])
admin = APIRouter(tags=["admin-pool-access-partners"])


class PartnerGrant(BaseModel):
    auth_id: str


class ScanInput(BaseModel):
    ticket: str


@admin.post("/{pool_id}/operators", status_code=201)
async def authorize_reception(pool_id: uuid.UUID, payload: PartnerGrant,
                              _admin: AuthUser=Depends(require_admin),
                              db: AsyncSession=Depends(get_async_db)):
    if not payload.auth_id.strip():
        raise HTTPException(422, "Operator auth id is required")
    existing = (await db.execute(select(PoolAccessPartnerOperator).where(
        PoolAccessPartnerOperator.pool_id == pool_id,
        PoolAccessPartnerOperator.auth_id == payload.auth_id.strip(),
    ))).scalar_one_or_none()
    if existing:
        existing.is_active = True
    else:
        existing = PoolAccessPartnerOperator(pool_id=pool_id, auth_id=payload.auth_id.strip(), is_active=True)
        db.add(existing)
    await db.commit()
    return {"id": str(existing.id), "pool_id": str(pool_id), "active": True}


@router.get("/assigned-pools")
async def assigned_pools(user: AuthUser=Depends(get_current_user),
                         db: AsyncSession=Depends(get_async_db)):
    rows = (await db.execute(select(PoolAccessPartnerOperator).where(
        PoolAccessPartnerOperator.auth_id == user.user_id,
        PoolAccessPartnerOperator.is_active.is_(True),
    ))).scalars().all()
    return [{"pool_id": str(r.pool_id)} for r in rows]


@router.post("/{pool_id}/redeem")
async def redeem_at_partner(pool_id: uuid.UUID, payload: ScanInput,
                            user: AuthUser=Depends(get_current_user),
                            db: AsyncSession=Depends(get_async_db)):
    allowed = (await db.execute(select(PoolAccessPartnerOperator).where(
        PoolAccessPartnerOperator.pool_id == pool_id,
        PoolAccessPartnerOperator.auth_id == user.user_id,
        PoolAccessPartnerOperator.is_active.is_(True),
    ))).scalar_one_or_none()
    if not allowed:
        raise HTTPException(403, "Not authorised for this pool")
    admission_id = admission_id_from_ticket(payload.ticket)
    admission = (await db.execute(select(PoolAccessAdmission).where(
        PoolAccessAdmission.id == admission_id).with_for_update())).scalar_one_or_none()
    if not admission:
        raise HTTPException(404, "Ticket not found")
    booking = await db.get(PoolAccessBooking, admission.booking_id)
    offer = await db.get(PoolAccessOffer, booking.offer_id)
    if offer.pool_id != pool_id:
        raise HTTPException(403, "Ticket belongs to another pool")
    if booking.status != "confirmed" or not booking.payment_reference:
        raise HTTPException(409, "Ticket is not paid")
    now = utc_now()
    if not (offer.starts_at <= now <= offer.ends_at):
        raise HTTPException(409, "Outside reserved admission window")
    if admission.checked_in_at:
        raise HTTPException(409, "Ticket has already been used")
    admission.checked_in_at = now
    admission.checked_in_by = user.user_id
    await db.commit()
    return {"status": "admitted", "guest_name": admission.guest_name,
            "admission_id": str(admission.id)}
