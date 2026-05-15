"""Admin CRUD + stats + by-auth lookup."""

"""Core members router - CRUD operations for member profiles."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_url, resolve_media_urls
from libs.common.supabase import get_supabase_admin_client
from libs.db.session import get_async_db
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.members_service.models import (
    ChallengeBadgeAward,
    CoachProfile,
    Member,
    MemberAvailability,
    MemberChallengeCompletion,
    MemberEmergencyContact,
    MemberMembership,
    MemberPreferences,
    MemberProfile,
    VolunteerInterest,
)
from services.members_service.routers._helpers import (
    member_eager_load_options,
    normalize_member_tiers,
    resolve_member_media_urls,
)
from services.members_service.schemas import (
    ChallengeBadgeAwardResponse,
    MemberBasicResponse,
    MemberCreate,
    MemberDirectoryResponse,
    MemberListResponse,
    MemberPublicResponse,
    MemberResponse,
    MemberUpdate,
)

logger = get_logger(__name__)
router = APIRouter()

@router.post("/", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def create_member(
    member_in: MemberCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Directly create a member (internal use or admin).
    Normal users should go through the pending registration flow.
    """
    query = select(Member).where(Member.email == member_in.email)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    member = Member(**member_in.model_dump())
    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()

@router.get("/", response_model=List[MemberListResponse])
async def list_members(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
):
    """List all members (admin use)."""
    query = (
        select(Member)
        .options(*member_eager_load_options())
        .offset(skip)
        .limit(limit)
        .order_by(Member.created_at.desc())
    )
    result = await db.execute(query)
    members = result.scalars().all()

    # Collect all media IDs for batch resolution
    media_ids = [m.profile_photo_media_id for m in members if m.profile_photo_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    responses: list[MemberListResponse] = []
    for member in members:
        base = MemberResponse.model_validate(member, from_attributes=True)
        payload = base.model_dump(
            exclude={
                "coach_profile",
                "profile",
                "membership",
                "emergency_contact",
                "availability",
                "preferences",
            }
        )
        payload["is_coach"] = bool(member.coach_profile)

        # Flatten profile fields
        if base.profile:
            p = base.profile
            payload["phone"] = p.phone
            payload["swim_level"] = p.swim_level
            payload["city"] = p.city
            payload["country"] = p.country
            payload["gender"] = p.gender
            payload["date_of_birth"] = p.date_of_birth
            payload["occupation"] = p.occupation
            payload["area_in_lagos"] = p.area_in_lagos
            payload["how_found_us"] = p.how_found_us
            payload["previous_communities"] = p.previous_communities
            payload["hopes_from_swimbuddz"] = p.hopes_from_swimbuddz
            payload["goals_narrative"] = p.personal_goals

        # Flatten membership fields
        if base.membership:
            m = base.membership
            payload["primary_tier"] = m.primary_tier
            payload["active_tiers"] = m.active_tiers
            payload["requested_tiers"] = m.requested_tiers
            payload["community_paid_until"] = m.community_paid_until
            payload["club_paid_until"] = m.club_paid_until
            payload["academy_paid_until"] = m.academy_paid_until

        # Flatten emergency contact
        if base.emergency_contact:
            ec = base.emergency_contact
            payload["emergency_contact_name"] = ec.name
            payload["emergency_contact_phone"] = ec.phone
            payload["medical_info"] = ec.medical_info

        # Add resolved photo URL
        if member.profile_photo_media_id:
            payload["profile_photo_url"] = url_map.get(member.profile_photo_media_id)

        responses.append(MemberListResponse(**payload))
    return responses

@router.get("/stats")
async def get_member_stats(
    db: AsyncSession = Depends(get_async_db),
):
    """Get member statistics."""
    query = select(func.count(Member.id))
    result = await db.execute(query)
    total_members = result.scalar_one() or 0

    query = select(func.count(Member.id)).where(Member.registration_complete.is_(True))
    result = await db.execute(query)
    active_members = result.scalar_one() or 0

    query = select(func.count(Member.id)).where(Member.approval_status == "approved")
    result = await db.execute(query)
    approved_members = result.scalar_one() or 0

    query = select(func.count(Member.id)).where(Member.approval_status == "pending")
    result = await db.execute(query)
    pending_approvals = result.scalar_one() or 0

    return {
        "total_members": total_members,
        "active_members": active_members,
        "approved_members": approved_members,
        "pending_approvals": pending_approvals,
    }

@router.get("/by-auth/{auth_id}", response_model=MemberResponse)
async def get_member_by_auth_id(
    auth_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a member by their Supabase auth_id.
    Used for service-to-service lookups (e.g., payments service).
    """
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    return member

@router.get("/{member_id}", response_model=MemberResponse)
async def get_member(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a member by ID (admin use)."""
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    # Resolve media URLs
    member_dict = MemberResponse.model_validate(member).model_dump()
    member_dict = await resolve_member_media_urls(member_dict)
    return member_dict

@router.patch("/{member_id}", response_model=MemberResponse)
async def update_member(
    member_id: uuid.UUID,
    member_in: MemberUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a member by ID (admin only)."""
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    update_data = member_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(member, field, value)

    db.add(member)
    await db.commit()
    await db.refresh(member)
    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    updated_member = result.scalar_one()

    # Resolve media URLs
    member_dict = MemberResponse.model_validate(updated_member).model_dump()
    member_dict = await resolve_member_media_urls(member_dict)
    return member_dict

@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a member by ID (admin only)."""
    query = select(Member).where(Member.id == member_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    # Delete from Supabase Auth
    try:
        supabase = get_supabase_admin_client()
        if member.auth_id:
            supabase.auth.admin.delete_user(member.auth_id)
            logger.info(
                "Deleted Supabase user",
                extra={"extra_fields": {"auth_id": member.auth_id}},
            )
    except Exception as e:
        logger.error(
            "Failed to delete Supabase user",
            extra={"extra_fields": {"auth_id": member.auth_id, "error": str(e)}},
        )

    # Delete all related sub-tables
    await db.execute(delete(MemberProfile).where(MemberProfile.member_id == member.id))
    await db.execute(
        delete(MemberEmergencyContact).where(
            MemberEmergencyContact.member_id == member.id
        )
    )
    await db.execute(
        delete(MemberAvailability).where(MemberAvailability.member_id == member.id)
    )
    await db.execute(
        delete(MemberMembership).where(MemberMembership.member_id == member.id)
    )
    await db.execute(
        delete(MemberPreferences).where(MemberPreferences.member_id == member.id)
    )
    await db.execute(delete(CoachProfile).where(CoachProfile.member_id == member.id))
    await db.execute(
        delete(VolunteerInterest).where(VolunteerInterest.member_id == member.id)
    )
    await db.execute(
        delete(MemberChallengeCompletion).where(
            MemberChallengeCompletion.member_id == member.id
        )
    )

    await db.delete(member)
    await db.commit()
    return None
