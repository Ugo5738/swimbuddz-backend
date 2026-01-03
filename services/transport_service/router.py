import uuid
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from pydantic import BaseModel, ConfigDict
from services.transport_service.models import (
    PickupLocation,
    RideArea,
    RideBooking,
    RouteInfo,
    SessionRideConfig,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/transport", tags=["transport"])


# RideArea Management Endpoints


class RideAreaCreate(BaseModel):
    name: str
    slug: str


class RideAreaUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    is_active: Optional[bool] = None


class PickupLocationBase(BaseModel):
    name: str
    description: Optional[str] = None
    address: Optional[str] = None  # Exact street address
    latitude: Optional[float] = None  # GPS coordinates
    longitude: Optional[float] = None


class PickupLocationCreate(PickupLocationBase):
    pass


class PickupLocationUpdate(PickupLocationBase):
    is_active: Optional[bool] = None


class PickupLocationResponse(PickupLocationBase):
    id: uuid.UUID
    area_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RideAreaResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    pickup_locations: List[PickupLocationResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RouteInfoBase(BaseModel):
    origin_area_id: Optional[uuid.UUID] = None
    origin_pickup_location_id: Optional[uuid.UUID] = None
    destination: str
    destination_name: str
    distance_text: str
    duration_text: str
    departure_offset_minutes: int = 120


class RouteInfoCreate(RouteInfoBase):
    pass


class RouteInfoUpdate(BaseModel):
    origin_area_id: Optional[uuid.UUID] = None
    origin_pickup_location_id: Optional[uuid.UUID] = None
    destination: Optional[str] = None
    destination_name: Optional[str] = None
    distance_text: Optional[str] = None
    duration_text: Optional[str] = None
    departure_offset_minutes: Optional[int] = None


class RouteInfoResponse(RouteInfoBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("/areas", response_model=List[RideAreaResponse])
async def list_ride_areas(
    db: AsyncSession = Depends(get_async_db),
):
    """List all active ride areas with their pickup locations."""
    query = select(RideArea).where(RideArea.is_active.is_(True)).order_by(RideArea.name)
    result = await db.execute(query)
    areas = result.scalars().all()

    # Fetch pickup locations for each area
    responses = []
    for area in areas:
        locs_query = select(PickupLocation).where(
            PickupLocation.area_id == area.id, PickupLocation.is_active.is_(True)
        )
        locs_result = await db.execute(locs_query)
        locations = locs_result.scalars().all()

        responses.append(
            RideAreaResponse(
                id=area.id,
                name=area.name,
                slug=area.slug,
                is_active=area.is_active,
                pickup_locations=[
                    PickupLocationResponse.model_validate(loc) for loc in locations
                ],
                created_at=area.created_at,
                updated_at=area.updated_at,
            )
        )

    return responses


@router.delete("/admin/members/{member_id}")
async def admin_delete_member_transport(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete transport bookings for a member (Admin only).
    """
    result = await db.execute(
        delete(RideBooking).where(RideBooking.member_id == member_id)
    )
    await db.commit()
    return {"deleted": result.rowcount or 0}


@router.post("/areas", response_model=RideAreaResponse)
async def create_ride_area(
    area_in: RideAreaCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new ride area."""
    area = RideArea(name=area_in.name, slug=area_in.slug, is_active=True)
    db.add(area)
    await db.commit()
    await db.refresh(area)

    return RideAreaResponse(
        id=area.id,
        name=area.name,
        slug=area.slug,
        is_active=area.is_active,
        pickup_locations=[],
        created_at=area.created_at,
        updated_at=area.updated_at,
    )


@router.get("/areas/{area_id}", response_model=RideAreaResponse)
async def get_ride_area(
    area_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get ride area details with pickup locations."""
    query = select(RideArea).where(RideArea.id == area_id)
    result = await db.execute(query)
    area = result.scalar_one_or_none()

    if not area:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Ride area not found")

    locs_query = select(PickupLocation).where(PickupLocation.area_id == area_id)
    locs_result = await db.execute(locs_query)
    locations = locs_result.scalars().all()

    return RideAreaResponse(
        id=area.id,
        name=area.name,
        slug=area.slug,
        is_active=area.is_active,
        pickup_locations=[
            PickupLocationResponse.model_validate(loc) for loc in locations
        ],
        created_at=area.created_at,
        updated_at=area.updated_at,
    )


@router.patch("/areas/{area_id}", response_model=RideAreaResponse)
async def update_ride_area(
    area_id: uuid.UUID,
    area_in: RideAreaUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update a ride area."""
    query = select(RideArea).where(RideArea.id == area_id)
    result = await db.execute(query)
    area = result.scalar_one_or_none()

    if not area:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Ride area not found")

    update_data = area_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(area, field, value)

    await db.commit()
    await db.refresh(area)

    locs_query = select(PickupLocation).where(PickupLocation.area_id == area_id)
    locs_result = await db.execute(locs_query)
    locations = locs_result.scalars().all()

    return RideAreaResponse(
        id=area.id,
        name=area.name,
        slug=area.slug,
        is_active=area.is_active,
        pickup_locations=[
            PickupLocationResponse.model_validate(loc) for loc in locations
        ],
        created_at=area.created_at,
        updated_at=area.updated_at,
    )


@router.delete("/areas/{area_id}", status_code=204)
async def delete_ride_area(
    area_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a ride area and its pickup locations."""
    query = select(RideArea).where(RideArea.id == area_id)
    result = await db.execute(query)
    area = result.scalar_one_or_none()

    if not area:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Ride area not found")

    # Delete pickup locations first to avoid FK constraint violation
    from sqlalchemy import delete

    await db.execute(delete(PickupLocation).where(PickupLocation.area_id == area_id))

    # Delete associated session ride configs
    await db.execute(
        delete(SessionRideConfig).where(SessionRideConfig.ride_area_id == area_id)
    )

    await db.delete(area)
    await db.commit()


@router.post("/areas/{area_id}/locations", response_model=PickupLocationResponse)
async def add_pickup_location(
    area_id: uuid.UUID,
    location_in: PickupLocationCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """Add a pickup location to a ride area."""
    # Verify area exists
    area_query = select(RideArea).where(RideArea.id == area_id)
    area_result = await db.execute(area_query)
    if not area_result.scalar_one_or_none():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Ride area not found")

    location = PickupLocation(
        area_id=area_id,
        name=location_in.name,
        description=location_in.description,
        address=location_in.address,
        latitude=location_in.latitude,
        longitude=location_in.longitude,
        is_active=True,
    )
    db.add(location)
    await db.commit()
    await db.refresh(location)

    return PickupLocationResponse.model_validate(location)


@router.patch("/locations/{location_id}", response_model=PickupLocationResponse)
async def update_pickup_location(
    location_id: uuid.UUID,
    location_in: PickupLocationUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update a pickup location."""
    query = select(PickupLocation).where(PickupLocation.id == location_id)
    result = await db.execute(query)
    location = result.scalar_one_or_none()

    if not location:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Pickup location not found")

    update_data = location_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(location, field, value)

    await db.commit()
    await db.refresh(location)

    return PickupLocationResponse.model_validate(location)


@router.delete("/locations/{location_id}", status_code=204)
async def delete_pickup_location(
    location_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a pickup location."""
    query = select(PickupLocation).where(PickupLocation.id == location_id)
    result = await db.execute(query)
    location = result.scalar_one_or_none()

    if not location:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Pickup location not found")

    await db.delete(location)
    await db.commit()


# Route info management (for distances/ETAs per area or pickup -> destination)


@router.get("/routes", response_model=List[RouteInfoResponse])
async def list_routes(
    origin_area_id: Optional[uuid.UUID] = None,
    origin_pickup_location_id: Optional[uuid.UUID] = None,
    destination: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db),
):
    query = select(RouteInfo)
    if origin_area_id:
        query = query.where(RouteInfo.origin_area_id == origin_area_id)
    if origin_pickup_location_id:
        query = query.where(
            RouteInfo.origin_pickup_location_id == origin_pickup_location_id
        )
    if destination:
        query = query.where(RouteInfo.destination == destination)

    result = await db.execute(query)
    routes = result.scalars().all()
    return [RouteInfoResponse.model_validate(r) for r in routes]


@router.post("/routes", response_model=RouteInfoResponse, status_code=201)
async def create_route(
    route_in: RouteInfoCreate,
    db: AsyncSession = Depends(get_async_db),
):
    route = RouteInfo(**route_in.model_dump())
    db.add(route)
    await db.commit()
    await db.refresh(route)
    return RouteInfoResponse.model_validate(route)


@router.patch("/routes/{route_id}", response_model=RouteInfoResponse)
async def update_route(
    route_id: uuid.UUID,
    route_in: RouteInfoUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    query = select(RouteInfo).where(RouteInfo.id == route_id)
    result = await db.execute(query)
    route = result.scalar_one_or_none()
    if not route:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Route not found")

    update_data = route_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(route, field, value)

    await db.commit()
    await db.refresh(route)
    return RouteInfoResponse.model_validate(route)


@router.delete("/routes/{route_id}", status_code=204)
async def delete_route(
    route_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    query = select(RouteInfo).where(RouteInfo.id == route_id)
    result = await db.execute(query)
    route = result.scalar_one_or_none()
    if not route:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Route not found")

    await db.delete(route)
    await db.commit()


# Session Ride Configuration Endpoints


class SessionRideConfigCreate(BaseModel):
    ride_area_id: uuid.UUID
    cost: float = 0.0
    capacity: int = 4
    departure_time: Optional[datetime] = None


class SessionRideConfigResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    ride_area_id: uuid.UUID
    ride_area_name: str  # Populated via join
    # Populated via join with availability and route info
    pickup_locations: List[Dict] = []
    cost: float
    capacity: int
    departure_time: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.post(
    "/sessions/{session_id}/ride-configs",
    response_model=List[SessionRideConfigResponse],
)
async def attach_ride_areas_to_session(
    session_id: uuid.UUID,
    configs_in: List[SessionRideConfigCreate],
    db: AsyncSession = Depends(get_async_db),
):
    """Attach ride areas to a session with session-specific configuration."""
    # Delete existing configs (replace strategy)
    query = select(SessionRideConfig).where(SessionRideConfig.session_id == session_id)
    result = await db.execute(query)
    existing = result.scalars().all()
    for cfg in existing:
        await db.delete(cfg)

    # Create new configs
    new_configs = []
    for cfg_data in configs_in:
        cfg = SessionRideConfig(
            session_id=session_id,
            ride_area_id=cfg_data.ride_area_id,
            cost=cfg_data.cost,
            capacity=cfg_data.capacity,
            departure_time=cfg_data.departure_time,
        )
        db.add(cfg)
        new_configs.append(cfg)

    await db.commit()

    # Fetch with joins to populate details
    responses = []
    for cfg in new_configs:
        await db.refresh(cfg)

        # Get ride area details
        area_query = select(RideArea).where(RideArea.id == cfg.ride_area_id)
        area_result = await db.execute(area_query)
        area = area_result.scalar_one()

        # Get pickup locations for this area
        locs_query = select(PickupLocation).where(
            PickupLocation.area_id == cfg.ride_area_id
        )
        locs_result = await db.execute(locs_query)
        locations = locs_result.scalars().all()

        responses.append(
            SessionRideConfigResponse(
                id=cfg.id,
                session_id=cfg.session_id,
                ride_area_id=cfg.ride_area_id,
                ride_area_name=area.name,
                pickup_locations=[
                    {
                        "id": str(loc.id),
                        "name": loc.name,
                        "description": loc.description,
                    }
                    for loc in locations
                ],
                cost=cfg.cost,
                capacity=cfg.capacity,
                departure_time=cfg.departure_time,
                created_at=cfg.created_at,
                updated_at=cfg.updated_at,
            )
        )

    return responses


@router.get(
    "/sessions/{session_id}/ride-configs",
    response_model=List[SessionRideConfigResponse],
)
async def get_session_ride_configs(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get ride configurations for a session with route info and pickup location
    availability."""
    # Get session info via API call to sessions service to maintain service decoupling
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # Call sessions service directly (without /api/v1 prefix which is only
            # for gateway)
            response = await client.get(
                f"http://sessions-service:8002/sessions/{session_id}"
            )
            response.raise_for_status()
            session_data = response.json()

            # Extract needed fields - sessions service uses starts_at, not start_time
            start_time_str = session_data.get("starts_at") or session_data.get(
                "start_time"
            )
            session_start_time = datetime.fromisoformat(
                start_time_str.replace("Z", "+00:00")
            )
            session_location = session_data["location"]
        except httpx.HTTPError:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Session not found")

    # Get session ride configs
    query = select(SessionRideConfig).where(SessionRideConfig.session_id == session_id)
    result = await db.execute(query)
    configs = result.scalars().all()

    responses = []
    for cfg in configs:
        # Get ride area details
        area_query = select(RideArea).where(RideArea.id == cfg.ride_area_id)
        area_result = await db.execute(area_query)
        area = area_result.scalar_one()

        # Get pickup locations
        locs_query = select(PickupLocation).where(
            PickupLocation.area_id == cfg.ride_area_id
        )
        locs_result = await db.execute(locs_query)
        locations = locs_result.scalars().all()

        # Check booking counts per pickup location to determine availability
        from sqlalchemy import func

        # Get booking counts for all pickup locations in this config
        booking_counts = {}
        active_pickup_location_id = None

        for loc in locations:
            count_query = select(func.count(RideBooking.id)).where(
                RideBooking.session_ride_config_id == cfg.id,
                RideBooking.pickup_location_id == loc.id,
            )
            count_result = await db.execute(count_query)
            count = count_result.scalar_one() or 0
            booking_counts[str(loc.id)] = count

            # If this location has bookings, it's the active one
            if count > 0 and active_pickup_location_id is None:
                active_pickup_location_id = str(loc.id)

        # Build pickup locations with availability info AND route info
        pickup_locations_data = []
        destination_val = session_location

        for loc in locations:
            loc_id = str(loc.id)
            current_bookings = booking_counts.get(loc_id, 0)

            # A location is available if:
            # 1. No location in this area has bookings yet, OR
            # 2. This is the location that has bookings AND it hasn't reached capacity
            is_available = False
            if active_pickup_location_id is None:
                # No bookings yet, all locations are available
                is_available = True
            elif active_pickup_location_id == loc_id:
                # This location has bookings, check if it has capacity
                is_available = current_bookings < cfg.capacity
            # else: Another location is active, this one is not available

            # Fetch route info for this specific pickup location
            route_query = select(RouteInfo).where(
                RouteInfo.origin_pickup_location_id == loc.id,
                RouteInfo.destination == destination_val,
            )
            route_result = await db.execute(route_query)
            route_info = route_result.scalar_one_or_none()

            # Calculate times
            loc_distance_text = None
            loc_duration_text = None
            loc_departure_time_calculated = None
            loc_arrival_time_calculated = None

            if route_info:
                loc_distance_text = route_info.distance_text
                loc_duration_text = route_info.duration_text

                from datetime import timedelta

                if session_start_time and route_info.departure_offset_minutes:
                    loc_departure_time_calculated = session_start_time - timedelta(
                        minutes=route_info.departure_offset_minutes
                    )
                    loc_arrival_time_calculated = session_start_time

            pickup_locations_data.append(
                {
                    "id": loc_id,
                    "name": loc.name,
                    "description": loc.description,
                    "is_available": is_available,
                    "current_bookings": current_bookings,
                    "max_capacity": cfg.capacity,
                    "distance_text": loc_distance_text,
                    "duration_text": loc_duration_text,
                    "departure_time_calculated": loc_departure_time_calculated,
                    "arrival_time_calculated": loc_arrival_time_calculated,
                }
            )

        responses.append(
            SessionRideConfigResponse(
                id=cfg.id,
                session_id=cfg.session_id,
                ride_area_id=cfg.ride_area_id,
                ride_area_name=area.name,
                pickup_locations=pickup_locations_data,
                cost=cfg.cost,
                capacity=cfg.capacity,
                departure_time=cfg.departure_time,
                created_at=cfg.created_at,
                updated_at=cfg.updated_at,
            )
        )

    return responses


# Ride Booking Endpoints


class RideBookingCreate(BaseModel):
    session_ride_config_id: uuid.UUID
    pickup_location_id: uuid.UUID


class RideBookingResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    member_id: uuid.UUID
    session_ride_config_id: uuid.UUID
    pickup_location_id: uuid.UUID
    pickup_location_name: str  # Populated
    ride_area_name: str  # Populated
    assigned_ride_number: int
    cost: float  # From config
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.post("/sessions/{session_id}/bookings", response_model=RideBookingResponse)
async def create_ride_booking(
    session_id: uuid.UUID,
    booking_in: RideBookingCreate,
    member_id: uuid.UUID,  # Passed from frontend or auth
    db: AsyncSession = Depends(get_async_db),
):
    """Create or update a ride booking for a member."""
    # Check existing
    query = select(RideBooking).where(
        RideBooking.session_id == session_id, RideBooking.member_id == member_id
    )
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    if existing:
        # Update
        existing.session_ride_config_id = booking_in.session_ride_config_id
        existing.pickup_location_id = booking_in.pickup_location_id
        await db.commit()
        await db.refresh(existing)
        booking = existing
    else:
        # Calculate ride number
        from sqlalchemy import func

        count_query = select(func.count(RideBooking.id)).where(
            RideBooking.session_ride_config_id == booking_in.session_ride_config_id,
            RideBooking.pickup_location_id == booking_in.pickup_location_id,
        )
        count_result = await db.execute(count_query)
        count = count_result.scalar_one() or 0

        # Get capacity from config
        cfg_query = select(SessionRideConfig).where(
            SessionRideConfig.id == booking_in.session_ride_config_id
        )
        cfg_result = await db.execute(cfg_query)
        cfg = cfg_result.scalar_one()
        capacity = cfg.capacity

        assigned_ride_number = (count // capacity) + 1

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

    # Get details for response
    cfg_query = (
        select(SessionRideConfig, RideArea)
        .join(RideArea)
        .where(SessionRideConfig.id == booking.session_ride_config_id)
    )
    cfg_result = await db.execute(cfg_query)
    cfg, area = cfg_result.one()

    loc_query = select(PickupLocation).where(
        PickupLocation.id == booking.pickup_location_id
    )
    loc_result = await db.execute(loc_query)
    location = loc_result.scalar_one()

    return RideBookingResponse(
        id=booking.id,
        session_id=booking.session_id,
        member_id=booking.member_id,
        session_ride_config_id=booking.session_ride_config_id,
        pickup_location_id=booking.pickup_location_id,
        pickup_location_name=location.name,
        ride_area_name=area.name,
        assigned_ride_number=booking.assigned_ride_number,
        cost=cfg.cost,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


@router.get(
    "/sessions/{session_id}/bookings/me", response_model=Optional[RideBookingResponse]
)
async def get_my_booking(
    session_id: uuid.UUID,
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get current member's booking for a session."""
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
    cfg, area = cfg_result.one()

    loc_query = select(PickupLocation).where(
        PickupLocation.id == booking.pickup_location_id
    )
    loc_result = await db.execute(loc_query)
    location = loc_result.scalar_one()

    return RideBookingResponse(
        id=booking.id,
        session_id=booking.session_id,
        member_id=booking.member_id,
        session_ride_config_id=booking.session_ride_config_id,
        pickup_location_id=booking.pickup_location_id,
        pickup_location_name=location.name,
        ride_area_name=area.name,
        assigned_ride_number=booking.assigned_ride_number,
        cost=cfg.cost,
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
        cfg, area = cfg_result.one()

        loc_query = select(PickupLocation).where(
            PickupLocation.id == booking.pickup_location_id
        )
        loc_result = await db.execute(loc_query)
        location = loc_result.scalar_one()

        responses.append(
            RideBookingResponse(
                id=booking.id,
                session_id=booking.session_id,
                member_id=booking.member_id,
                session_ride_config_id=booking.session_ride_config_id,
                pickup_location_id=booking.pickup_location_id,
                pickup_location_name=location.name,
                ride_area_name=area.name,
                assigned_ride_number=booking.assigned_ride_number,
                cost=cfg.cost,
                created_at=booking.created_at,
                updated_at=booking.updated_at,
            )
        )

    return responses
