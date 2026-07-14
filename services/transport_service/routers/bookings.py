"""Ride booking routes."""

import uuid
from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import get_current_user, is_admin_or_service
from libs.auth.models import AuthUser
from libs.common.currency import kobo_to_bubbles_exact
from libs.common.service_client import debit_member_wallet
from libs.common.service_client.sessions import (
    get_confirmed_booking_for_session_member,
)
from libs.db.session import get_async_db
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.transport_service.models import (
    MemberRef,
    PickupLocation,
    RideArea,
    RideBooking,
    RidePassenger,
    RidePassengerType,
    SessionRideConfig,
)
from services.transport_service.routers._helpers import (
    _raise_wallet_error,
    get_current_member,
    get_member_or_override,
)
from services.transport_service.services.chat_sync import (
    ensure_trip_channel,
    reconcile_trip_membership,
)

router = APIRouter(prefix="/transport", tags=["transport"])


class RidePassengerInput(BaseModel):
    passenger_type: RidePassengerType
    full_name: Optional[str] = Field(default=None, max_length=160)

    @field_validator("full_name")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        normalized = (value or "").strip()
        return normalized or None


class RidePassengerResponse(RidePassengerInput):
    id: uuid.UUID
    position: int


class RideBookingCreate(BaseModel):
    session_ride_config_id: uuid.UUID
    pickup_location_id: uuid.UUID
    pay_with_bubbles: bool = False  # If True, debit wallet for the ride cost
    num_seats: int = Field(
        default=1, ge=1, le=20, description="Number of seats to book (1-20)"
    )
    passengers: Optional[List[RidePassengerInput]] = None

    @model_validator(mode="after")
    def manifest_matches_seat_count(self):
        if self.passengers is None:
            return self
        if len(self.passengers) != self.num_seats:
            raise ValueError("Passenger manifest must contain one entry per seat")
        member_count = sum(
            passenger.passenger_type == RidePassengerType.MEMBER
            for passenger in self.passengers
        )
        if member_count > 1:
            raise ValueError("A ride booking can contain at most one booking member")
        return self


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
    passengers: List[RidePassengerResponse]
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
        passengers=[
            RidePassengerResponse(
                id=passenger.id,
                passenger_type=passenger.passenger_type,
                full_name=passenger.full_name,
                position=passenger.position,
            )
            for passenger in booking.passengers
        ],
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


def _passenger_manifest(booking_in: RideBookingCreate) -> List[RidePassengerInput]:
    if booking_in.passengers is not None:
        return booking_in.passengers
    return [
        RidePassengerInput(passenger_type=RidePassengerType.MEMBER),
        *[
            RidePassengerInput(passenger_type=RidePassengerType.OBSERVER)
            for _ in range(booking_in.num_seats - 1)
        ],
    ]


async def _replace_passengers(
    db: AsyncSession,
    booking: RideBooking,
    manifest: List[RidePassengerInput],
) -> None:
    await db.execute(
        delete(RidePassenger).where(RidePassenger.ride_booking_id == booking.id)
    )
    for position, passenger in enumerate(manifest, start=1):
        db.add(
            RidePassenger(
                ride_booking_id=booking.id,
                passenger_type=passenger.passenger_type,
                full_name=passenger.full_name,
                position=position,
            )
        )


@router.post("/sessions/{session_id}/bookings", response_model=RideBookingResponse)
async def create_ride_booking(
    session_id: uuid.UUID,
    booking_in: RideBookingCreate,
    member_id: Optional[uuid.UUID] = Query(
        None, description="Member ID override for service-to-service calls"
    ),
    allow_without_booking: bool = Query(
        False,
        description="Explicit service-role override for a verified day-of walk-in",
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
    manifest = _passenger_manifest(booking_in)

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
    if cfg_for_cost.session_id != session_id:
        raise HTTPException(
            status_code=400,
            detail="Ride config does not belong to this session",
        )
    pickup = (
        await db.execute(
            select(PickupLocation).where(
                PickupLocation.id == booking_in.pickup_location_id,
                PickupLocation.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if pickup is None or pickup.area_id != cfg_for_cost.ride_area_id:
        raise HTTPException(
            status_code=400,
            detail="Pickup location is not valid for this ride",
        )

    is_service = current_user.role == "service_role"
    is_admin = is_admin_or_service(current_user) and not is_service
    has_operator_override = is_admin or (is_service and allow_without_booking)
    if allow_without_booking and not is_service:
        raise HTTPException(
            status_code=403,
            detail="Only an internal service can authorize a walk-in ride booking",
        )
    if not has_operator_override:
        try:
            confirmed_booking = await get_confirmed_booking_for_session_member(
                session_id=str(session_id),
                member_id=str(resolved_member_id),
                calling_service="transport",
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503,
                detail="Could not verify the session booking. Please try again.",
            ) from exc
        if confirmed_booking is None:
            raise HTTPException(
                status_code=403,
                detail="Book this session before adding a ride share.",
            )

    # Track the previous ride config for chat-channel reconciliation if the
    # member is moving between trips on the same session.
    previous_config_id: Optional[uuid.UUID] = (
        existing.session_ride_config_id if existing else None
    )

    if existing:
        # Passenger details and pickup point can be corrected without changing
        # the paid seat/configuration entitlement. Seat or priced-route changes
        # require cancellation and a fresh checkout.
        if existing.num_seats != booking_in.num_seats:
            raise HTTPException(
                status_code=409,
                detail="Seat count cannot be changed after booking. Cancel and rebook.",
            )
        if existing.session_ride_config_id != booking_in.session_ride_config_id:
            raise HTTPException(
                status_code=409,
                detail="Ride route cannot be changed after booking. Cancel and rebook.",
            )
        existing.pickup_location_id = booking_in.pickup_location_id
        await _replace_passengers(db, existing, manifest)
        await db.commit()
        await db.refresh(existing)
        await db.refresh(existing, attribute_names=["passengers"])
        booking = existing
    else:
        # Debit wallet for new bookings when requested and ride has a cost
        if booking_in.pay_with_bubbles and cfg_for_cost.cost > 0:
            try:
                fee_bubbles = kobo_to_bubbles_exact(
                    cfg_for_cost.cost * booking_in.num_seats
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This ride cannot be paid entirely with whole Bubbles. "
                        "Use mixed Bubbles and card payment instead."
                    ),
                ) from exc
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
        await db.flush()
        await _replace_passengers(db, booking, manifest)
        await db.commit()
        await db.refresh(booking)
        await db.refresh(booking, attribute_names=["passengers"])

    cfg, area, location = await _get_booking_details(db, booking)

    # Sync chat membership for the trip channel. Best-effort — chat downtime
    # never blocks the booking.
    if (
        previous_config_id is not None
        and previous_config_id != booking.session_ride_config_id
    ):
        # Member moved trips on the same session — drop them from the old
        # ride channel before adding to the new one.
        await reconcile_trip_membership(
            session_ride_config_id=previous_config_id,
            member_id=booking.member_id,
            booking_id=booking.id,
            action="remove",
        )

    await ensure_trip_channel(
        session_ride_config_id=booking.session_ride_config_id,
        area_name=area.name if area else None,
    )
    await reconcile_trip_membership(
        session_ride_config_id=booking.session_ride_config_id,
        member_id=booking.member_id,
        booking_id=booking.id,
        action="add",
    )

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
