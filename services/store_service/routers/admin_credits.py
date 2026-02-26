"""Admin store pickup locations and store credits router."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.store_service.models import (
    AuditEntityType,
    PickupLocation,
    StoreCredit,
)
from services.store_service.routers._helpers import log_audit
from services.store_service.schemas import (
    PickupLocationCreate,
    PickupLocationResponse,
    PickupLocationUpdate,
    StoreCreditCreate,
    StoreCreditResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["admin-store"])


# ============================================================================
# PICKUP LOCATIONS
# ============================================================================


@router.get("/pickup-locations", response_model=list[PickupLocationResponse])
async def list_all_pickup_locations(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all pickup locations (including inactive)."""
    query = select(PickupLocation).order_by(
        PickupLocation.sort_order, PickupLocation.name
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/pickup-locations",
    response_model=PickupLocationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_pickup_location(
    location_in: PickupLocationCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new pickup location."""
    location = PickupLocation(**location_in.model_dump())
    db.add(location)
    await db.commit()
    await db.refresh(location)

    await log_audit(
        db,
        AuditEntityType.PICKUP_LOCATION,
        location.id,
        "created",
        current_user.user_id,
        new_value=location_in.model_dump(),
    )
    await db.commit()

    return location


@router.patch("/pickup-locations/{location_id}", response_model=PickupLocationResponse)
async def update_pickup_location(
    location_id: uuid.UUID,
    location_in: PickupLocationUpdate,
    current_user: AuthUser = Depends(require_admin),
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

    await log_audit(
        db,
        AuditEntityType.PICKUP_LOCATION,
        location.id,
        "updated",
        current_user.user_id,
        new_value=update_data,
    )

    await db.commit()
    await db.refresh(location)
    return location


@router.delete(
    "/pickup-locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_pickup_location(
    location_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Deactivate a pickup location."""
    query = select(PickupLocation).where(PickupLocation.id == location_id)
    result = await db.execute(query)
    location = result.scalar_one_or_none()

    if not location:
        raise HTTPException(status_code=404, detail="Pickup location not found")

    location.is_active = False
    await log_audit(
        db,
        AuditEntityType.PICKUP_LOCATION,
        location.id,
        "deactivated",
        current_user.user_id,
    )
    await db.commit()
    return None


# ============================================================================
# STORE CREDITS
# ============================================================================


@router.get("/credits", response_model=list[StoreCreditResponse])
async def list_all_store_credits(
    member_auth_id: Optional[str] = None,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all store credits."""
    query = select(StoreCredit).order_by(StoreCredit.created_at.desc())

    if member_auth_id:
        query = query.where(StoreCredit.member_auth_id == member_auth_id)

    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/credits", response_model=StoreCreditResponse, status_code=status.HTTP_201_CREATED
)
async def create_store_credit(
    credit_in: StoreCreditCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Issue a manual store credit."""
    credit = StoreCredit(
        member_auth_id=credit_in.member_auth_id,
        amount_ngn=credit_in.amount_ngn,
        balance_ngn=credit_in.amount_ngn,
        source_type=credit_in.source_type,
        source_order_id=credit_in.source_order_id,
        reason=credit_in.reason,
        expires_at=credit_in.expires_at,
        issued_by=current_user.user_id,
    )
    db.add(credit)

    await log_audit(
        db,
        AuditEntityType.STORE_CREDIT,
        credit.id,
        "issued",
        current_user.user_id,
        new_value={
            "amount_ngn": float(credit_in.amount_ngn),
            "member_auth_id": credit_in.member_auth_id,
            "source_type": credit_in.source_type.value,
        },
        notes=credit_in.reason,
    )

    await db.commit()
    await db.refresh(credit)
    return credit
