"""Volunteer profile self-service endpoints (get / register / update)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from services.volunteer_service.models import VolunteerProfile
from services.volunteer_service.schemas import (
    VolunteerProfileCreate,
    VolunteerProfileResponse,
    VolunteerProfileUpdate,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/profile/me", response_model=VolunteerProfileResponse)
async def get_my_profile(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Get my volunteer profile."""
    member = await get_member_by_auth_id(user.user_id, calling_service="volunteer")
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(member["id"])
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=404, detail="Volunteer profile not found. Register first."
        )
    return profile


@router.post("/profile/me", response_model=VolunteerProfileResponse, status_code=201)
async def register_as_volunteer(
    data: VolunteerProfileCreate,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Register as a volunteer."""
    member = await get_member_by_auth_id(user.user_id, calling_service="volunteer")
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(member["id"])

    existing = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Already registered as a volunteer")

    profile = VolunteerProfile(
        member_id=member_id,
        preferred_roles=data.preferred_roles,
        available_days=data.available_days,
        notes=data.notes,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.patch("/profile/me", response_model=VolunteerProfileResponse)
async def update_my_profile(
    data: VolunteerProfileUpdate,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Update my volunteer preferences."""
    member = await get_member_by_auth_id(user.user_id, calling_service="volunteer")
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(member["id"])
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Volunteer profile not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile
