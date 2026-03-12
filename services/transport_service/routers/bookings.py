"""Ride booking routes."""

import uuid
from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.currency import kobo_to_bubbles
from libs.common.service_client import debit_member_wallet
from libs.db.session import get_async_db
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    get_member_or_override,
)

router = APIRouter(prefix="/transport", tags=["transport"])


class RideBookingCreate(BaseModel):
    session_ride_config_id: uuid.UUID
    pickup_location_id: uuid.UUID
    pay_with_bubbles: bool = False  # If True, debit wallet for the ride cost
    num_seats: int = Field(default=1, ge=1, description="Number of seats to book (1+)")


class RideBookingResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    member_id: uuid.UUID
    session_ride_config_id: uuid.UUID
    pickup_location_id: uuid.UUID
    pickup_location_name: str  # Populated
    ride_area_name: str  # Populated
    assigned_ride_number: int
    num_seats: int
    cost: float  # Total cost for all seats — naira (kobo converted on read)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


def _build_response(
    booking: RideBooking,
    cfg: Optional[SessionRideConfig],
    area: Optional[RideArea],
    location: Optional[PickupLocation],
) -> RideBookingResponse:
    """Build a RideBookingResponse from a booking + joined entities."""
    return RideBookingResponse(
        id=booking.id,
        session_id=booking.session_id,
        member_id=booking.member_id,
        session_ride_config_id=booking.session_ride_config_id,
        pickup_location_id=booking.pickup_location_id,
        pickup_location_name=location.name if location else "Unknown Location",
        ride_area_name=area.name if area else "Unknown Area",
        assigned_ride_number=booking.assigned_ride_number,
        num_seats=booking.num_seats,
        cost=(cfg.cost * booking.num_seats / 100.0) if cfg else 0.0,  # kobo → naira
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


async def _get_booking_details(
    db: AsyncSession, booking: RideBooking
) -> tuple[Optional[SessionRideConfig], Optional[RideArea], Optional[PickupLocation]]:
    """Fetch the config, area, and pickup location for a booking."""
    cfg_query = (
        select(SessionRideConfig, RideArea)
        .join(RideArea)
        .where(SessionRideConfig.id == booking.session_ride_config_id)
    )
    cfg_result = await db.execute(cfg_query)
    row = cfg_result.first()
    cfg, area = (row[0], row[1]) if row else (None, None)

    loc_result = await db.execute(
        select(PickupLocation).where(PickupLocation.id == booking.pickup_location_id)
    )
    location = loc_result.scalar_one_or_none()

    return cfg, area, location


@router.post("/sessions/{session_id}/bookings", response_model=RideBookingResponse)
async def create_ride_booking(
    session_id: uuid.UUID,
    booking_in: RideBookingCreate,
    member_id: Optional[uuid.UUID] = Query(
        None, description="Member ID override for service-to-service calls"
    ),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Create or update a ride booking for the authenticated member.

    Supports multi-seat bookings (``num_seats`` >= 1).  Service-to-service
    callers (e.g. payments entitlement handler) pass ``member_id`` as a query
    param together with a service-role JWT.
    """
    current_member = await get_member_or_override(member_id, current_user, db)
    resolved_member_id = current_member.id

    # Check existing booking
    query = select(RideBooking).where(
        RideBooking.session_id == session_id,
        RideBooking.member_id == resolved_member_id,
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
        # Update existing booking (no re-charge) — allows changing location
        existing.session_ride_config_id = booking_in.session_ride_config_id
        existing.pickup_location_id = booking_in.pickup_location_id
        await db.commit()
        await db.refresh(existing)
        booking = existing
    else:
        # Debit wallet for new bookings when requested and ride has a cost
        if booking_in.pay_with_bubbles and cfg_for_cost.cost > 0:
            fee_bubbles = kobo_to_bubbles(cfg_for_cost.cost * booking_in.num_seats)
            idempotency_key = f"ride-{session_id}-{resolved_member_id}"
            try:
                await debit_member_wallet(
                    current_member.auth_id,
                    amount=fee_bubbles,
                    idempotency_key=idempotency_key,
                    description=f"Ride share booking ({booking_in.num_seats} seat{'s' if booking_in.num_seats > 1 else ''}) — {fee_bubbles} 🫧",
                    calling_service="transport",
                    transaction_type="purchase",
                    reference_type="ride_booking",
                    reference_id=str(session_id),
                )
            except httpx.HTTPStatusError as e:
                _raise_wallet_error(e)

        # Calculate ride number based on total SEATS (not bookings)
        seats_query = select(func.coalesce(func.sum(RideBooking.num_seats), 0)).where(
            RideBooking.session_ride_config_id == booking_in.session_ride_config_id,
            RideBooking.pickup_location_id == booking_in.pickup_location_id,
        )
        seats_result = await db.execute(seats_query)
        total_seats = seats_result.scalar_one() or 0
        assigned_ride_number = (total_seats // cfg_for_cost.capacity) + 1

        booking = RideBooking(
            session_id=session_id,
            member_id=resolved_member_id,
            session_ride_config_id=booking_in.session_ride_config_id,
            pickup_location_id=booking_in.pickup_location_id,
            assigned_ride_number=assigned_ride_number,
            num_seats=booking_in.num_seats,
        )
        db.add(booking)
        await db.commit()
        await db.refresh(booking)

    cfg, area, location = await _get_booking_details(db, booking)
    return _build_response(booking, cfg, area, location)


@router.get(
    "/sessions/{session_id}/bookings/me", response_model=Optional[RideBookingResponse]
)
async def get_my_booking(
    session_id: uuid.UUID,
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the authenticated member's booking for a session."""
    query = select(RideBooking).where(
        RideBooking.session_id == session_id,
        RideBooking.member_id == current_member.id,
    )
    result = await db.execute(query)
    booking = result.scalar_one_or_none()

    if not booking:
        return None

    cfg, area, location = await _get_booking_details(db, booking)
    return _build_response(booking, cfg, area, location)


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
        cfg, area, location = await _get_booking_details(db, booking)
        responses.append(_build_response(booking, cfg, area, location))

    return responses
