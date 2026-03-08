"""Admin pool management routes — full CRUD for all pools."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.pools_service.models import PartnershipStatus, Pool, PoolType
from services.pools_service.schemas import (
    PoolCreate,
    PoolListResponse,
    PoolResponse,
    PoolUpdate,
)

router = APIRouter(tags=["admin-pools"])


# ---------------------------------------------------------------------------
# LIST
# ---------------------------------------------------------------------------


@router.get("", response_model=PoolListResponse)
async def list_pools(
    partnership_status: Optional[PartnershipStatus] = None,
    pool_type: Optional[PoolType] = None,
    location_area: Optional[str] = None,
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all pools with optional filters."""
    query = select(Pool)

    if partnership_status is not None:
        query = query.where(Pool.partnership_status == partnership_status)
    if pool_type is not None:
        query = query.where(Pool.pool_type == pool_type)
    if location_area:
        query = query.where(Pool.location_area.ilike(f"%{location_area}%"))
    if search:
        query = query.where(Pool.name.ilike(f"%{search}%"))
    if is_active is not None:
        query = query.where(Pool.is_active == is_active)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    query = query.order_by(Pool.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    pools = (await db.execute(query)).scalars().all()

    return PoolListResponse(
        items=pools,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------


@router.post("", response_model=PoolResponse, status_code=status.HTTP_201_CREATED)
async def create_pool(
    pool_in: PoolCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new pool entry."""
    # Check slug uniqueness
    existing = await db.execute(select(Pool).where(Pool.slug == pool_in.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Pool with slug '{pool_in.slug}' already exists",
        )

    pool = Pool(**pool_in.model_dump())
    db.add(pool)
    await db.commit()
    await db.refresh(pool)

    return pool


# ---------------------------------------------------------------------------
# GET DETAIL
# ---------------------------------------------------------------------------


@router.get("/{pool_id}", response_model=PoolResponse)
async def get_pool(
    pool_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get pool detail."""
    result = await db.execute(select(Pool).where(Pool.id == pool_id))
    pool = result.scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    return pool


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------


@router.patch("/{pool_id}", response_model=PoolResponse)
async def update_pool(
    pool_id: uuid.UUID,
    pool_in: PoolUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a pool."""
    result = await db.execute(select(Pool).where(Pool.id == pool_id))
    pool = result.scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    update_data = pool_in.model_dump(exclude_unset=True)
    if not update_data:
        return pool

    # Check slug uniqueness if changing
    if "slug" in update_data and update_data["slug"] != pool.slug:
        existing = await db.execute(
            select(Pool).where(Pool.slug == update_data["slug"])
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Pool with slug '{update_data['slug']}' already exists",
            )

    for field, value in update_data.items():
        setattr(pool, field, value)

    await db.commit()
    await db.refresh(pool)

    return pool


# ---------------------------------------------------------------------------
# UPDATE PARTNERSHIP STATUS
# ---------------------------------------------------------------------------


@router.post("/{pool_id}/status", response_model=PoolResponse)
async def update_partnership_status(
    pool_id: uuid.UUID,
    partnership_status: PartnershipStatus,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a pool's partnership status."""
    result = await db.execute(select(Pool).where(Pool.id == pool_id))
    pool = result.scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    pool.partnership_status = partnership_status
    await db.commit()
    await db.refresh(pool)

    return pool


# ---------------------------------------------------------------------------
# SOFT DELETE
# ---------------------------------------------------------------------------


@router.delete("/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pool(
    pool_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Soft-delete a pool (set is_active=False)."""
    result = await db.execute(select(Pool).where(Pool.id == pool_id))
    pool = result.scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")

    pool.is_active = False
    await db.commit()

    return None
