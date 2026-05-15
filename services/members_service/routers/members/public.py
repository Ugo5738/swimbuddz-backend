"""Public + directory member lookups."""

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

@router.get("/public", response_model=List[MemberPublicResponse])
async def list_public_members(
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all members for public dropdown (no auth required).
    Returns limited info (id, first_name, last_name).
    """
    query = select(Member).order_by(Member.first_name, Member.last_name)
    result = await db.execute(query)
    return result.scalars().all()

@router.get("/directory", response_model=List[MemberDirectoryResponse])
async def list_directory_members(
    db: AsyncSession = Depends(get_async_db),
):
    """
    List members who opted into the community directory.
    Filters server-side by show_in_directory=True.
    Returns only the fields needed for the directory page.
    No auth required — directory is a community feature.
    """
    from sqlalchemy.orm import selectinload

    query = (
        select(Member)
        .join(MemberProfile, MemberProfile.member_id == Member.id)
        .where(MemberProfile.show_in_directory.is_(True))
        .where(Member.is_active.is_(True))
        .where(Member.registration_complete.is_(True))
        .options(selectinload(Member.profile))
        .order_by(Member.first_name, Member.last_name)
    )
    result = await db.execute(query)
    members = result.scalars().all()

    # Batch-resolve profile photo URLs
    media_ids = [m.profile_photo_media_id for m in members if m.profile_photo_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    responses: list[MemberDirectoryResponse] = []
    for member in members:
        profile = member.profile
        responses.append(
            MemberDirectoryResponse(
                id=member.id,
                first_name=member.first_name,
                last_name=member.last_name,
                profile_photo_url=(
                    url_map.get(str(member.profile_photo_media_id))
                    if member.profile_photo_media_id
                    else None
                ),
                city=profile.city if profile else None,
                country=profile.country if profile else None,
                swim_level=profile.swim_level if profile else None,
                interest_tags=profile.interest_tags if profile else None,
            )
        )
    return responses

@router.get("/public/{member_id}")
async def get_member_for_verification(
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get member data for public verification (e.g., pool staff scanning QR code).
    Returns limited info for verification purposes only.
    No authentication required.
    """
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

    # Resolve profile photo URL
    profile_photo_url = await resolve_media_url(member.profile_photo_media_id)

    # Return only necessary verification data
    membership = member.membership
    return {
        "id": str(member.id),
        "first_name": member.first_name,
        "last_name": member.last_name,
        "email": member.email,  # For staff to verify identity
        "profile_photo_url": profile_photo_url,
        "created_at": member.created_at.isoformat() if member.created_at else None,
        "membership": (
            {
                "active_tiers": membership.active_tiers if membership else [],
                "community_paid_until": (
                    membership.community_paid_until.isoformat()
                    if membership and membership.community_paid_until
                    else None
                ),
                "club_paid_until": (
                    membership.club_paid_until.isoformat()
                    if membership and membership.club_paid_until
                    else None
                ),
                "academy_paid_until": (
                    membership.academy_paid_until.isoformat()
                    if membership and membership.academy_paid_until
                    else None
                ),
            }
            if membership
            else None
        ),
    }
