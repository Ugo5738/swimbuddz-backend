"""Public pool routes — read-only, active partner pools only."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.pools_service.models import (
    OperatingArea,
    PartnershipStatus,
    Pool,
    PoolType,
)
from services.pools_service.schemas import (
    OperatingAreaResponse,
    PoolListResponse,
    PoolResponse,
)

router = APIRouter(tags=["pools"])


@router.get("", response_model=PoolListResponse)
async def list_partner_pools(
    pool_type: Optional[PoolType] = None,
    location_area: Optional[str] = None,
    operating_area_id: Optional[uuid.UUID] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """List active partner pools (public-facing)."""
    query = select(Pool).where(
        Pool.partnership_status == PartnershipStatus.ACTIVE_PARTNER,
        Pool.is_active.is_(True),
    )

    if pool_type is not None:
        query = query.where(Pool.pool_type == pool_type)
    if location_area:
        query = query.where(Pool.location_area.ilike(f"%{location_area}%"))
    if operating_area_id:
        query = query.where(Pool.operating_area_id == operating_area_id)
    if search:
        query = query.where(Pool.name.ilike(f"%{search}%"))

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    query = query.order_by(Pool.name)
    query = query.offset((page - 1) * page_size).limit(page_size)

    pools = (await db.execute(query)).scalars().all()

    return PoolListResponse(
        items=pools,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/operating-areas", response_model=list[OperatingAreaResponse])
async def list_public_operating_areas(
    db: AsyncSession = Depends(get_async_db),
):
    """List active areas that currently contain a bookable partner pool.

    Club registration uses this as the first step of its Area -> Pool -> Plan
    selector. Areas without an active partner pool are deliberately hidden so
    a swimmer cannot choose a location where SwimBuddz cannot yet operate.
    """

    rows = await db.execute(
        select(OperatingArea)
        .join(Pool, Pool.operating_area_id == OperatingArea.id)
        .where(
            OperatingArea.is_active.is_(True),
            Pool.partnership_status == PartnershipStatus.ACTIVE_PARTNER,
            Pool.is_active.is_(True),
        )
        .distinct()
        .order_by(OperatingArea.country_code, OperatingArea.name)
    )
    return rows.scalars().all()


@router.get("/{pool_id}", response_model=PoolResponse)
async def get_partner_pool(
    pool_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get active partner pool detail."""
    result = await db.execute(
        select(Pool).where(
            Pool.id == pool_id,
            Pool.partnership_status == PartnershipStatus.ACTIVE_PARTNER,
            Pool.is_active.is_(True),
        )
    )
    pool = result.scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    return pool
