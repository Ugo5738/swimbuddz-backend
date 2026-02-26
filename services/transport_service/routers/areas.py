"""Ride area and pickup location management routes."""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from pydantic import BaseModel, ConfigDict
from services.transport_service.models import (
    PickupLocation,
    RideArea,
    RideBooking,
    SessionRideConfig,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/transport", tags=["transport"])


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
        raise HTTPException(status_code=404, detail="Ride area not found")

    # Delete pickup locations first to avoid FK constraint violation
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
        raise HTTPException(status_code=404, detail="Pickup location not found")

    await db.delete(location)
    await db.commit()
