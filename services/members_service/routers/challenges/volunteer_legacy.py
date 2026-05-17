"""Legacy volunteer-role + interest endpoints (admin-gated).

Originally the volunteer-role system lived in members_service. The active
volunteer programme has since moved to volunteer_service; these tables
are now `legacy_volunteer_*` and these endpoints are admin-only so the
legacy surface cannot be exercised anonymously while the legacy data
lingers.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import VolunteerInterest, VolunteerRole
from services.members_service.schemas import (
    VolunteerInterestCreate,
    VolunteerInterestResponse,
    VolunteerRoleCreate,
    VolunteerRoleResponse,
    VolunteerRoleUpdate,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/roles", response_model=List[VolunteerRoleResponse])
async def list_volunteer_roles(
    active_only: bool = Query(True, description="Show only active roles"),
    db: AsyncSession = Depends(get_async_db),
):
    """List volunteer roles with optional filters."""
    query = select(VolunteerRole)

    if active_only:
        query = query.where(VolunteerRole.is_active.is_(True))

    query = query.order_by(VolunteerRole.created_at.desc())

    result = await db.execute(query)
    roles = result.scalars().all()

    # Get interested member counts for each role
    roles_with_counts = []
    for role in roles:
        interest_query = select(func.count(VolunteerInterest.id)).where(
            VolunteerInterest.role_id == role.id
        )
        interest_result = await db.execute(interest_query)
        interested_count = interest_result.scalar_one()

        role_dict = role.__dict__.copy()
        role_dict["interested_count"] = interested_count
        roles_with_counts.append(VolunteerRoleResponse.model_validate(role_dict))

    return roles_with_counts


@router.get("/roles/{role_id}", response_model=VolunteerRoleResponse)
async def get_volunteer_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single volunteer role by ID."""
    query = select(VolunteerRole).where(VolunteerRole.id == role_id)
    result = await db.execute(query)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Get interested count
    interest_query = select(func.count(VolunteerInterest.id)).where(
        VolunteerInterest.role_id == role.id
    )
    interest_result = await db.execute(interest_query)
    interested_count = interest_result.scalar_one()

    role_dict = role.__dict__.copy()
    role_dict["interested_count"] = interested_count

    return VolunteerRoleResponse.model_validate(role_dict)


@router.post("/roles", response_model=VolunteerRoleResponse, status_code=201)
async def create_volunteer_role(
    role_data: VolunteerRoleCreate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Create a new volunteer role (admin only)."""
    role = VolunteerRole(**role_data.model_dump())

    db.add(role)
    await db.commit()
    await db.refresh(role)

    role_dict = role.__dict__.copy()
    role_dict["interested_count"] = 0

    return VolunteerRoleResponse.model_validate(role_dict)


@router.patch("/roles/{role_id}", response_model=VolunteerRoleResponse)
async def update_volunteer_role(
    role_id: uuid.UUID,
    role_data: VolunteerRoleUpdate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Update a volunteer role (admin only)."""
    query = select(VolunteerRole).where(VolunteerRole.id == role_id)
    result = await db.execute(query)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Update only provided fields
    for field, value in role_data.model_dump(exclude_unset=True).items():
        setattr(role, field, value)

    await db.commit()
    await db.refresh(role)

    # Get interested count
    interest_query = select(func.count(VolunteerInterest.id)).where(
        VolunteerInterest.role_id == role.id
    )
    interest_result = await db.execute(interest_query)
    interested_count = interest_result.scalar_one()

    role_dict = role.__dict__.copy()
    role_dict["interested_count"] = interested_count

    return VolunteerRoleResponse.model_validate(role_dict)


@router.delete("/roles/{role_id}", status_code=204)
async def delete_volunteer_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Delete a volunteer role (admin only)."""
    query = select(VolunteerRole).where(VolunteerRole.id == role_id)
    result = await db.execute(query)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Delete associated interests first
    await db.execute(
        select(VolunteerInterest).where(VolunteerInterest.role_id == role_id)
    )
    await db.delete(role)
    await db.commit()

    return None


@router.post("/interest", response_model=VolunteerInterestResponse, status_code=201)
async def register_volunteer_interest(
    interest_data: VolunteerInterestCreate,
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Register interest in a volunteer role (legacy; admin-only).

    NOTE: VolunteerRole/VolunteerInterest tables are LEGACY (renamed to
    legacy_volunteer_*) and the active volunteer programme lives in
    volunteer_service. This endpoint is gated to admin so the legacy
    surface cannot be exercised anonymously while the legacy data lingers.
    """
    # Check if role exists
    role_query = select(VolunteerRole).where(VolunteerRole.id == interest_data.role_id)
    role_result = await db.execute(role_query)
    role = role_result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Check if already interested
    existing_query = select(VolunteerInterest).where(
        VolunteerInterest.role_id == interest_data.role_id,
        VolunteerInterest.member_id == member_id,
    )
    existing_result = await db.execute(existing_query)
    existing_interest = existing_result.scalar_one_or_none()

    if existing_interest:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Already registered interest in this role",
        )

    interest = VolunteerInterest(
        role_id=interest_data.role_id, member_id=member_id, notes=interest_data.notes
    )

    db.add(interest)
    await db.commit()
    await db.refresh(interest)

    return VolunteerInterestResponse.model_validate(interest)


@router.get(
    "/roles/{role_id}/interested", response_model=List[VolunteerInterestResponse]
)
async def list_interested_members(
    role_id: uuid.UUID,
    status: Optional[str] = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """List members interested in a volunteer role (admin only)."""
    query = select(VolunteerInterest).where(VolunteerInterest.role_id == role_id)

    if status:
        query = query.where(VolunteerInterest.status == status)

    result = await db.execute(query)
    interests = result.scalars().all()

    return [
        VolunteerInterestResponse.model_validate(interest) for interest in interests
    ]
