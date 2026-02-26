"""Route info and session ride configuration routes."""

import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from libs.common.currency import naira_to_kobo
from libs.db.session import get_async_db
from pydantic import BaseModel, ConfigDict
from services.transport_service.models import (
    PickupLocation,
    RideArea,
    RideBooking,
    RouteInfo,
    SessionRideConfig,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/transport", tags=["transport"])


# Route info management (for distances/ETAs per area or pickup -> destination)


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


class SessionRideConfigCreate(BaseModel):
    ride_area_id: uuid.UUID
    cost: float = 0.0  # Naira (float) — router converts to kobo on write
    capacity: int = 4
    departure_time: Optional[datetime] = None


class SessionRideConfigResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    ride_area_id: uuid.UUID
    ride_area_name: str  # Populated via join
    # Populated via join with availability and route info
    pickup_locations: List[Dict] = []
    cost: float  # Naira (float) — converted from kobo on read
    capacity: int
    departure_time: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


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
        raise HTTPException(status_code=404, detail="Route not found")

    await db.delete(route)
    await db.commit()


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
            cost=naira_to_kobo(cfg_data.cost),  # Store as kobo integer
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
                cost=cfg.cost / 100.0,  # kobo → naira for API response
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
                cost=cfg.cost / 100.0,  # kobo → naira for API response
                capacity=cfg.capacity,
                departure_time=cfg.departure_time,
                created_at=cfg.created_at,
                updated_at=cfg.updated_at,
            )
        )

    return responses
