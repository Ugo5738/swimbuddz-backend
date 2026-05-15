"""Admin: profile listing, lookup, update + spotlight feature/unfeature."""

import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.member_utils import resolve_member_basic, resolve_members_basic
from libs.db.session import get_async_db
from services.volunteer_service.models import VolunteerProfile, VolunteerTier
from services.volunteer_service.schemas import (
    FeatureVolunteerRequest,
    VolunteerProfileAdminUpdate,
    VolunteerProfileResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/profiles", response_model=list[VolunteerProfileResponse])
async def list_profiles(
    tier: Optional[VolunteerTier] = None,
    active_only: bool = True,
    skip: int = 0,
    limit: int = 50,
    admin: Annotated[AuthUser, Depends(require_admin)] = None,
    db: AsyncSession = Depends(get_async_db),
):
    q = select(VolunteerProfile).offset(skip).limit(limit)
    if tier:
        q = q.where(VolunteerProfile.tier == tier)
    if active_only:
        q = q.where(VolunteerProfile.is_active.is_(True))
    q = q.order_by(VolunteerProfile.total_hours.desc())

    profiles = (await db.execute(q)).scalars().all()

    # Batch-resolve member names via HTTP
    member_ids = [p.member_id for p in profiles]
    member_map = await resolve_members_basic(member_ids) if member_ids else {}

    results = []
    for p in profiles:
        data = {c.key: getattr(p, c.key) for c in p.__table__.columns}
        info = member_map.get(str(p.member_id))
        data["member_name"] = info.full_name if info else None
        data["member_email"] = info.email if info else None
        results.append(data)
    return results


@router.get("/profiles/{member_id}", response_model=VolunteerProfileResponse)
async def get_profile(
    member_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    data = {c.key: getattr(profile, c.key) for c in profile.__table__.columns}
    info = await resolve_member_basic(member_id)
    data["member_name"] = info.full_name if info else None
    data["member_email"] = info.email if info else None
    return data


@router.patch("/profiles/{member_id}", response_model=VolunteerProfileResponse)
async def admin_update_profile(
    member_id: uuid.UUID,
    data: VolunteerProfileAdminUpdate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.post(
    "/profiles/{member_id}/feature",
    response_model=VolunteerProfileResponse,
)
async def feature_volunteer(
    member_id: uuid.UUID,
    data: FeatureVolunteerRequest,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    """Feature a volunteer for the public spotlight. Un-features any currently featured volunteer."""
    # Un-feature all currently featured
    current_featured = (
        (
            await db.execute(
                select(VolunteerProfile).where(VolunteerProfile.is_featured.is_(True))
            )
        )
        .scalars()
        .all()
    )
    for p in current_featured:
        p.is_featured = False

    # Feature the target
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile.is_featured = True
    profile.featured_from = datetime.now(timezone.utc)
    profile.featured_until = data.featured_until
    if data.spotlight_quote is not None:
        profile.spotlight_quote = data.spotlight_quote

    await db.commit()
    await db.refresh(profile)

    result = {c.key: getattr(profile, c.key) for c in profile.__table__.columns}
    member_info = await resolve_members_basic([member_id])
    info = member_info.get(str(member_id))
    result["member_name"] = info.full_name if info else None
    result["member_email"] = info.email if info else None
    return result


@router.delete("/profiles/{member_id}/feature", status_code=204)
async def unfeature_volunteer(
    member_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    """Remove a volunteer from the spotlight."""
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    profile.is_featured = False
    await db.commit()
