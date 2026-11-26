"""Route info and session ride configuration routes."""

import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.currency import naira_to_kobo
from libs.common.service_client.sessions import get_session_by_id
from libs.db.session import get_async_db
from services.transport_service.models import (
    PickupLocation,
    RideArea,
    RideBooking,
    RouteInfo,
    SessionRideConfig,
)

router = APIRouter(prefix="/transport", tags=["transport"])


# Route info management (for distances/ETAs per area or pickup -> destination)


class RouteInfoBase(BaseModel):
    origin_area_id: Optional[uuid.UUID] = None
    origin_pickup_location_id: Optional[uuid.UUID] = None
    # Preferred destination link — pool from the pools registry.
    destination_pool_id: Optional[uuid.UUID] = None
    # Legacy string (matches Session.location enum); optional now.
    destination: Optional[str] = None
    destination_name: str
    distance_text: str
    duration_text: str
    departure_offset_minutes: int = 120


class RouteInfoCreate(RouteInfoBase):
    pass


class RouteInfoUpdate(BaseModel):
    origin_area_id: Optional[uuid.UUID] = None
    origin_pickup_location_id: Optional[uuid.UUID] = None
    destination_pool_id: Optional[uuid.UUID] = None
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
    destination_pool_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_async_db),
    _user: AuthUser = Depends(get_current_user),
):
    query = select(RouteInfo)
    if origin_area_id:
        query = query.where(RouteInfo.origin_area_id == origin_area_id)
    if origin_pickup_location_id:
        query = query.where(
            RouteInfo.origin_pickup_location_id == origin_pickup_location_id
        )
    if destination_pool_id:
        query = query.where(RouteInfo.destination_pool_id == destination_pool_id)
    if destination:
        query = query.where(RouteInfo.destination == destination)

    result = await db.execute(query)
    routes = result.scalars().all()
    return [RouteInfoResponse.model_validate(r) for r in routes]


@router.post("/routes", response_model=RouteInfoResponse, status_code=201)
async def create_route(
    route_in: RouteInfoCreate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
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
    _admin: AuthUser = Depends(require_admin),
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
    _admin: AuthUser = Depends(require_admin),
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
    _admin: AuthUser = Depends(require_admin),
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


# ─── Batch endpoint ──────────────────────────────────────────────────────


class SlimPickupLocation(BaseModel):
    id: uuid.UUID
    name: str


class SlimRideConfig(BaseModel):
    id: uuid.UUID
    ride_area_name: str
    cost: float
    capacity: int
    pickup_locations: List[SlimPickupLocation]


class BatchRideConfigsRequest(BaseModel):
    session_ids: List[uuid.UUID]


class BatchRideConfigsResponse(BaseModel):
    """Map of session_id (str) -> list of slim ride configs."""

    configs: Dict[str, List[SlimRideConfig]]


@router.post(
    "/sessions/ride-configs/batch",
    response_model=BatchRideConfigsResponse,
)
async def get_ride_configs_batch(
    body: BatchRideConfigsRequest,
    db: AsyncSession = Depends(get_async_db),
    _user: AuthUser = Depends(get_current_user),
):
    """Batch-fetch ride configs for multiple sessions in a single request.

    Returns a slim payload (no booking counts, no route timing) suitable for
    session list views. For full detail (availability, ETAs), use the
    per-session endpoint.

    Collapses what was previously N HTTP requests + N*(5-10) DB queries into
    1 request + 3 batched DB queries.
    """
    if not body.session_ids:
        return BatchRideConfigsResponse(configs={})

    # Dedup + cap defensive limit
    ids = list({sid for sid in body.session_ids})[:200]

    # 1. One query for all session ride configs
    configs = (
        (
            await db.execute(
                select(SessionRideConfig).where(SessionRideConfig.session_id.in_(ids))
            )
        )
        .scalars()
        .all()
    )

    if not configs:
        return BatchRideConfigsResponse(configs={str(sid): [] for sid in ids})

    # 2. One query for all involved ride areas
    area_ids = list({cfg.ride_area_id for cfg in configs})
    areas = (
        (await db.execute(select(RideArea).where(RideArea.id.in_(area_ids))))
        .scalars()
        .all()
    )
    area_name_by_id = {area.id: area.name for area in areas}

    # 3. One query for all pickup locations for those areas
    locations = (
        (
            await db.execute(
                select(PickupLocation).where(PickupLocation.area_id.in_(area_ids))
            )
        )
        .scalars()
        .all()
    )
    locations_by_area: Dict[uuid.UUID, List[PickupLocation]] = {}
    for loc in locations:
        locations_by_area.setdefault(loc.area_id, []).append(loc)

    # Compose response grouped by session_id
    result: Dict[str, List[SlimRideConfig]] = {str(sid): [] for sid in ids}
    for cfg in configs:
        pickup_locations = [
            SlimPickupLocation(id=loc.id, name=loc.name)
            for loc in locations_by_area.get(cfg.ride_area_id, [])
        ]
        result[str(cfg.session_id)].append(
            SlimRideConfig(
                id=cfg.id,
                ride_area_name=area_name_by_id.get(cfg.ride_area_id, ""),
                cost=cfg.cost / 100.0,  # kobo → naira
                capacity=cfg.capacity,
                pickup_locations=pickup_locations,
            )
        )

    return BatchRideConfigsResponse(configs=result)


@router.get(
    "/sessions/{session_id}/ride-configs",
    response_model=List[SessionRideConfigResponse],
)
async def get_session_ride_configs(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    _user: AuthUser = Depends(get_current_user),
):
    """Get ride configurations for a session with route info and pickup location
    availability.

    Short-circuits immediately when the session has no ride configurations
    (zero cross-service HTTP calls) and batches RideArea, PickupLocation,
    RideBooking counts, and RouteInfo lookups into constant SQL queries
    (ADR-025).
    """
    # 1. Check SessionRideConfig rows first so sessions without ride share
    # return [] immediately without an extra HTTP hop to sessions_service.
    configs = list(
        (
            await db.execute(
                select(SessionRideConfig).where(
                    SessionRideConfig.session_id == session_id
                )
            )
        )
        .scalars()
        .all()
    )
    if not configs:
        return []

    # 2. Fetch session metadata (starts_at + pool_id/location) for route ETA calculation.
    session_data = await get_session_by_id(str(session_id), calling_service="transport")
    if session_data is None:
        raise HTTPException(status_code=404, detail="Session not found")

    start_time_str = session_data.get("starts_at") or session_data.get("start_time")
    session_start_time = (
        datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        if start_time_str
        else None
    )
    raw_pool_id = session_data.get("pool_id")
    session_pool_id: Optional[uuid.UUID] = None
    if raw_pool_id:
        try:
            session_pool_id = uuid.UUID(str(raw_pool_id))
        except ValueError:
            session_pool_id = None
    session_location = session_data.get("location")

    # 3. Batch-load RideAreas, PickupLocations, grouped RideBooking counts, and RouteInfo.
    area_ids = list({cfg.ride_area_id for cfg in configs})
    config_ids = [cfg.id for cfg in configs]

    areas_by_id = {
        area.id: area
        for area in (
            await db.execute(select(RideArea).where(RideArea.id.in_(area_ids)))
        )
        .scalars()
        .all()
    }

    all_locations = list(
        (
            await db.execute(
                select(PickupLocation).where(PickupLocation.area_id.in_(area_ids))
            )
        )
        .scalars()
        .all()
    )
    locations_by_area: dict[uuid.UUID, list[PickupLocation]] = {}
    for loc in all_locations:
        locations_by_area.setdefault(loc.area_id, []).append(loc)

    booking_counts_by_key: dict[tuple[uuid.UUID, uuid.UUID], int] = {}
    if config_ids:
        count_rows = (
            await db.execute(
                select(
                    RideBooking.session_ride_config_id,
                    RideBooking.pickup_location_id,
                    func.count(RideBooking.id),
                )
                .where(RideBooking.session_ride_config_id.in_(config_ids))
                .group_by(
                    RideBooking.session_ride_config_id,
                    RideBooking.pickup_location_id,
                )
            )
        ).all()
        for cfg_id, loc_id, count in count_rows:
            booking_counts_by_key[(cfg_id, loc_id)] = int(count or 0)

    route_by_pool: dict[uuid.UUID, RouteInfo] = {}
    route_by_dest: dict[uuid.UUID, RouteInfo] = {}
    all_loc_ids = [loc.id for loc in all_locations]
    if all_loc_ids and (session_pool_id or session_location):
        dest_filters = []
        if session_pool_id:
            dest_filters.append(RouteInfo.destination_pool_id == session_pool_id)
        if session_location:
            dest_filters.append(RouteInfo.destination == session_location)
        routes = (
            (
                await db.execute(
                    select(RouteInfo).where(
                        RouteInfo.origin_pickup_location_id.in_(all_loc_ids),
                        or_(*dest_filters),
                    )
                )
            )
            .scalars()
            .all()
        )
        for route in routes:
            origin_id = route.origin_pickup_location_id
            if origin_id is None:
                continue
            if (
                session_pool_id
                and route.destination_pool_id is not None
                and str(route.destination_pool_id) == str(session_pool_id)
                and origin_id not in route_by_pool
            ):
                route_by_pool[origin_id] = route
            if (
                session_location
                and route.destination == session_location
                and origin_id not in route_by_dest
            ):
                route_by_dest[origin_id] = route

    responses = []
    for cfg in configs:
        area = areas_by_id.get(cfg.ride_area_id)
        if area is None:
            continue

        locations = locations_by_area.get(cfg.ride_area_id, [])

        # Determine active pickup location (first location with existing bookings)
        active_pickup_location_id: Optional[str] = None
        for loc in locations:
            if booking_counts_by_key.get((cfg.id, loc.id), 0) > 0:
                active_pickup_location_id = str(loc.id)
                break

        pickup_locations_data = []
        for loc in locations:
            loc_id = str(loc.id)
            current_bookings = booking_counts_by_key.get((cfg.id, loc.id), 0)

            # A location is available if:
            # 1. No location in this area has bookings yet, OR
            # 2. This is the active location AND it hasn't reached capacity
            if active_pickup_location_id is None:
                is_available = True
            elif active_pickup_location_id == loc_id:
                is_available = current_bookings < cfg.capacity
            else:
                is_available = False

            # Prefer pool_id route match; fall back to legacy destination string.
            route_info = route_by_pool.get(loc.id) or route_by_dest.get(loc.id)

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
