"""Ride booking routes."""

import uuid
from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from libs.common.currency import kobo_to_bubbles
from libs.common.service_client import debit_member_wallet
from libs.db.session import get_async_db
from pydantic import BaseModel, ConfigDict
from services.transport_service.models import (
    MemberRef,
    PickupLocation,
    RideArea,
    RideBooking,
    SessionRideConfig,
)
from services.transport_service.routers._helpers import (
    _raise_wallet_error,
    get_current_member,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/transport", tags=["transport"])


class RideBookingCreate(BaseModel):
    session_ride_config_id: uuid.UUID
    pickup_location_id: uuid.UUID
    pay_with_bubbles: bool = False  # If True, debit wallet for the ride cost


class RideBookingResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    member_id: uuid.UUID
    session_ride_config_id: uuid.UUID
    pickup_location_id: uuid.UUID
    pickup_location_name: str  # Populated
    ride_area_name: str  # Populated
    assigned_ride_number: int
    cost: float  # From config — naira (kobo converted on read)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.post("/sessions/{session_id}/bookings", response_model=RideBookingResponse)
async def create_ride_booking(
    session_id: uuid.UUID,
    booking_in: RideBookingCreate,
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """Create or update a ride booking for the authenticated member."""
    member_id = current_member.id

    # Check existing booking
    query = select(RideBooking).where(
        RideBooking.session_id == session_id, RideBooking.member_id == member_id
    )
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    # Always fetch the ride config upfront (needed for cost + capacity)
    cfg_query = select(SessionRideConfig).where(
        SessionRideConfig.id == booking_in.session_ride_config_id
    )
    cfg_result = await db.execute(cfg_query)
    cfg_for_cost = cfg_result.scalar_one_or_none()
    if not cfg_for_cost:
        raise HTTPException(status_code=404, detail="Ride config not found")

    if existing:
        # Update existing booking (no re-charge)
        existing.session_ride_config_id = booking_in.session_ride_config_id
        existing.pickup_location_id = booking_in.pickup_location_id
        await db.commit()
        await db.refresh(existing)
        booking = existing
    else:
        # Debit wallet for new bookings when requested and ride has a cost
        if booking_in.pay_with_bubbles and cfg_for_cost.cost > 0:
            fee_bubbles = kobo_to_bubbles(cfg_for_cost.cost)
            idempotency_key = f"ride-{session_id}-{member_id}"
            try:
                await debit_member_wallet(
                    current_member.auth_id,
                    amount=fee_bubbles,
                    idempotency_key=idempotency_key,
                    description=f"Ride share booking — {fee_bubbles} 🫧",
                    calling_service="transport",
                    transaction_type="purchase",
                    reference_type="ride_booking",
                    reference_id=str(session_id),
                )
            except httpx.HTTPStatusError as e:
                _raise_wallet_error(e)

        # Calculate ride number
        count_query = select(func.count(RideBooking.id)).where(
            RideBooking.session_ride_config_id == booking_in.session_ride_config_id,
            RideBooking.pickup_location_id == booking_in.pickup_location_id,
        )
        count_result = await db.execute(count_query)
        count = count_result.scalar_one() or 0
        assigned_ride_number = (count // cfg_for_cost.capacity) + 1

        booking = RideBooking(
            session_id=session_id,
            member_id=member_id,
            session_ride_config_id=booking_in.session_ride_config_id,
            pickup_location_id=booking_in.pickup_location_id,
            assigned_ride_number=assigned_ride_number,
        )
        db.add(booking)
        await db.commit()
        await db.refresh(booking)

    # Get details for response (with join on RideArea)
    cfg_query = (
        select(SessionRideConfig, RideArea)
        .join(RideArea)
        .where(SessionRideConfig.id == booking.session_ride_config_id)
    )
    cfg_result = await db.execute(cfg_query)
    row = cfg_result.first()
    if not row:
        cfg, area = None, None
    else:
        cfg, area = row

    loc_query = select(PickupLocation).where(
        PickupLocation.id == booking.pickup_location_id
    )
    loc_result = await db.execute(loc_query)
    location = loc_result.scalar_one_or_none()

    return RideBookingResponse(
        id=booking.id,
        session_id=booking.session_id,
        member_id=booking.member_id,
        session_ride_config_id=booking.session_ride_config_id,
        pickup_location_id=booking.pickup_location_id,
        pickup_location_name=location.name if location else "Unknown Location",
        ride_area_name=area.name if area else "Unknown Area",
        assigned_ride_number=booking.assigned_ride_number,
        cost=(cfg.cost / 100.0) if cfg else 0.0,  # kobo → naira
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


@router.get(
    "/sessions/{session_id}/bookings/me", response_model=Optional[RideBookingResponse]
)
async def get_my_booking(
    session_id: uuid.UUID,
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the authenticated member's booking for a session."""
    member_id = current_member.id
    query = select(RideBooking).where(
        RideBooking.session_id == session_id, RideBooking.member_id == member_id
    )
    result = await db.execute(query)
    booking = result.scalar_one_or_none()

    if not booking:
        return None

    # Get details
    cfg_query = (
        select(SessionRideConfig, RideArea)
        .join(RideArea)
        .where(SessionRideConfig.id == booking.session_ride_config_id)
    )
    cfg_result = await db.execute(cfg_query)
    row = cfg_result.first()
    if not row:
        cfg, area = None, None
    else:
        cfg, area = row

    loc_query = select(PickupLocation).where(
        PickupLocation.id == booking.pickup_location_id
    )
    loc_result = await db.execute(loc_query)
    location = loc_result.scalar_one_or_none()

    return RideBookingResponse(
        id=booking.id,
        session_id=booking.session_id,
        member_id=booking.member_id,
        session_ride_config_id=booking.session_ride_config_id,
        pickup_location_id=booking.pickup_location_id,
        pickup_location_name=location.name if location else "Unknown Location",
        ride_area_name=area.name if area else "Unknown Area",
        assigned_ride_number=booking.assigned_ride_number,
        cost=(cfg.cost / 100.0) if cfg else 0.0,  # kobo → naira
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


@router.get("/sessions/{session_id}/bookings", response_model=List[RideBookingResponse])
async def list_session_bookings(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """List all ride bookings for a session."""
    query = select(RideBooking).where(RideBooking.session_id == session_id)
    result = await db.execute(query)
    bookings = result.scalars().all()

    responses = []
    for booking in bookings:
        # Get details
        cfg_query = (
            select(SessionRideConfig, RideArea)
            .join(RideArea)
            .where(SessionRideConfig.id == booking.session_ride_config_id)
        )
        cfg_result = await db.execute(cfg_query)
        row = cfg_result.first()
        if not row:
            cfg, area = None, None
        else:
            cfg, area = row

        loc_query = select(PickupLocation).where(
            PickupLocation.id == booking.pickup_location_id
        )
        loc_result = await db.execute(loc_query)
        location = loc_result.scalar_one_or_none()

        responses.append(
            RideBookingResponse(
                id=booking.id,
                session_id=booking.session_id,
                member_id=booking.member_id,
                session_ride_config_id=booking.session_ride_config_id,
                pickup_location_id=booking.pickup_location_id,
                pickup_location_name=location.name if location else "Unknown Location",
                ride_area_name=area.name if area else "Unknown Area",
                assigned_ride_number=booking.assigned_ride_number,
                cost=(cfg.cost / 100.0) if cfg else 0.0,  # kobo → naira
                created_at=booking.created_at,
                updated_at=booking.updated_at,
            )
        )

    return responses
