"""Pool Access offers and provisional bookings.

Money cannot be confirmed here: paid activation must be wired to verified
payment-service fulfillment, so this slice is intentionally fail-closed.
"""

import uuid
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from libs.common.datetime_utils import utc_now
from services.pools_service.models import Pool, PartnershipStatus
from services.pools_service.models.access import (
    PoolAccessOffer,
    PoolAccessBooking,
    PoolAccessAdmission,
)
from services.pools_service.schemas.access import (
    OfferInput,
    OfferOut,
    AdminOfferOut,
    BookingInput,
    BookingOut,
)
from services.pools_service.services.access_policy import quoted_amount

public = APIRouter(tags=["pool-access"])
admin = APIRouter(tags=["admin-pool-access"])


@public.get("/offers", response_model=list[OfferOut])
async def published_offers(
    location_area: str | None = None, db: AsyncSession = Depends(get_async_db)
):
    now = utc_now()
    result = await db.execute(
        select(PoolAccessOffer, Pool)
        .join(Pool, Pool.id == PoolAccessOffer.pool_id)
        .where(
            PoolAccessOffer.status == "published",
            PoolAccessOffer.public_booking_enabled.is_(True),
            PoolAccessOffer.self_directed_permitted.is_(True),
            PoolAccessOffer.starts_at > now,
            Pool.is_active.is_(True),
            Pool.partnership_status == PartnershipStatus.ACTIVE_PARTNER,
        )
        .order_by(PoolAccessOffer.starts_at)
        .limit(100)
    )
    rows = result.all()
    return [
        OfferOut.model_validate(offer).model_copy(
            update={
                "pool_name": pool.name,
                "location_area": pool.location_area,
                "pool_address": pool.address,
                "pool_length_m": pool.pool_length_m,
                "depth_min_m": pool.depth_min_m,
                "depth_max_m": pool.depth_max_m,
                "has_lifeguard": pool.has_lifeguard,
            }
        )
        for offer, pool in rows
        if not location_area
        or location_area.casefold() in (pool.location_area or "").casefold()
    ]


@admin.get("/offers", response_model=list[AdminOfferOut])
async def admin_list_offers(
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(PoolAccessOffer).order_by(PoolAccessOffer.created_at.desc()).limit(200)
    )
    return result.scalars().all()


@admin.get("/bookings")
async def admin_list_access_bookings(
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    from services.pools_service.models.access import PoolAccessReconciliation

    result = await db.execute(
        select(PoolAccessBooking, PoolAccessOffer, PoolAccessReconciliation)
        .join(PoolAccessOffer, PoolAccessBooking.offer_id == PoolAccessOffer.id)
        .outerjoin(
            PoolAccessReconciliation,
            PoolAccessReconciliation.booking_id == PoolAccessBooking.id,
        )
        .order_by(PoolAccessBooking.created_at.desc())
        .limit(200)
    )
    return [
        {
            "booking_id": str(booking.id),
            "pool_id": str(offer.pool_id),
            "offer_title": offer.title,
            "buyer_email": booking.buyer_email,
            "headcount": booking.headcount,
            "status": booking.status,
            "revenue_kobo": booking.selling_total_kobo
            if booking.status == "confirmed"
            else 0,
            "currency": booking.currency,
            "payment_reference": booking.payment_reference,
            "verified_admissions": recon.verified_admissions if recon else None,
            "partner_payable_kobo": recon.payable_kobo if recon else None,
            "visit_end_at": offer.ends_at.isoformat(),
        }
        for booking, offer, recon in result.all()
    ]


@admin.post("/offers", response_model=AdminOfferOut, status_code=201)
async def create_offer(
    body: OfferInput,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    pool = await db.get(Pool, body.pool_id)
    if (
        not pool
        or not pool.is_active
        or pool.partnership_status != PartnershipStatus.ACTIVE_PARTNER
    ):
        raise HTTPException(400, "Select an active partner pool")
    offer = PoolAccessOffer(**body.model_dump(), status="draft")
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return offer


@admin.post("/offers/{offer_id}/publish", response_model=AdminOfferOut)
async def publish_offer(
    offer_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    offer = await db.get(PoolAccessOffer, offer_id)
    if not offer:
        raise HTTPException(404, "Offer not found")
    if not offer.self_directed_permitted or not offer.public_booking_enabled:
        raise HTTPException(
            409, "Self-directed visits and public inventory must be explicitly enabled"
        )
    if offer.starts_at <= utc_now():
        raise HTTPException(409, "Offer must start in the future")
    pool = await db.get(Pool, offer.pool_id)
    if not pool or not pool.is_active or pool.has_lifeguard is not True:
        raise HTTPException(
            409, "Pool Access requires a confirmed lifeguard-enabled facility"
        )
    if not offer.access_rules.strip() or not offer.cancellation_policy.strip():
        raise HTTPException(409, "Publish safety and cancellation terms first")
    offer.status = "published"
    await db.commit()
    await db.refresh(offer)
    return offer


@public.post("/bookings", response_model=BookingOut, status_code=201)
async def reserve_access(
    body: BookingInput,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Temporary reservation; NEVER issues a valid QR or paid entitlement."""
    if not user.email:
        raise HTTPException(400, "Account email is required")
    prior = await db.execute(
        select(PoolAccessBooking).where(
            PoolAccessBooking.buyer_auth_id == user.user_id,
            PoolAccessBooking.idempotency_key == body.idempotency_key,
        )
    )
    existing = prior.scalar_one_or_none()
    if existing:
        if existing.offer_id != body.offer_id or existing.headcount != len(body.guests):
            raise HTTPException(409, "Idempotency key already used for another booking")
        return existing
    offer = (
        await db.execute(
            select(PoolAccessOffer)
            .join(Pool, Pool.id == PoolAccessOffer.pool_id)
            .where(
                PoolAccessOffer.id == body.offer_id,
                PoolAccessOffer.status == "published",
                PoolAccessOffer.public_booking_enabled.is_(True),
                PoolAccessOffer.self_directed_permitted.is_(True),
                Pool.is_active.is_(True),
                Pool.partnership_status == PartnershipStatus.ACTIVE_PARTNER,
            )
            .with_for_update(of=PoolAccessOffer)
        )
    ).scalar_one_or_none()
    if not offer or offer.starts_at <= utc_now():
        raise HTTPException(409, "This visit is not available")
    used = (
        await db.execute(
            select(func.coalesce(func.sum(PoolAccessBooking.headcount), 0)).where(
                PoolAccessBooking.offer_id == offer.id,
                (PoolAccessBooking.status == "confirmed")
                | (
                    (PoolAccessBooking.status == "pending_payment")
                    & (PoolAccessBooking.hold_expires_at > utc_now())
                ),
            )
        )
    ).scalar_one()
    if used + len(body.guests) > offer.capacity:
        raise HTTPException(409, "Not enough available places")
    booking = PoolAccessBooking(
        offer_id=offer.id,
        buyer_auth_id=user.user_id,
        buyer_email=str(user.email),
        idempotency_key=body.idempotency_key,
        headcount=len(body.guests),
        selling_total_kobo=quoted_amount(offer.selling_price_kobo, len(body.guests)),
        negotiated_cost_kobo=offer.negotiated_cost_kobo,
        cost_basis=offer.cost_basis,
        currency=offer.currency,
        status="pending_payment",
        hold_expires_at=utc_now() + timedelta(minutes=15),
    )
    db.add(booking)
    await db.flush()
    for i, guest in enumerate(body.guests, 1):
        db.add(
            PoolAccessAdmission(booking_id=booking.id, ordinal=i, guest_name=guest.name)
        )
    await db.commit()
    await db.refresh(booking)
    return booking


@public.get("/bookings/me", response_model=list[BookingOut])
async def my_bookings(
    user: AuthUser = Depends(get_current_user), db: AsyncSession = Depends(get_async_db)
):
    rows = await db.execute(
        select(PoolAccessBooking)
        .where(PoolAccessBooking.buyer_auth_id == user.user_id)
        .order_by(PoolAccessBooking.created_at.desc())
        .limit(100)
    )
    return rows.scalars().all()
