"""Bulk member-record lookup (admin/service-to-service)."""

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

@router.post("/bulk-basic", response_model=dict[str, MemberBasicResponse])
async def get_members_bulk_basic(
    member_ids: list[uuid.UUID],
    db: AsyncSession = Depends(get_async_db),
):
    """
    Bulk lookup of basic member info by IDs.

    Internal endpoint for service-to-service calls. Returns a dict mapping
    member_id (string) -> basic info (name, email, profile photo).
    Max 50 IDs per request.
    """
    if len(member_ids) > 50:
        raise HTTPException(
            status_code=400, detail="Maximum 50 member IDs per request."
        )
    if not member_ids:
        return {}

    query = (
        select(Member)
        .where(Member.id.in_(member_ids))
        .options(selectinload(Member.membership))
    )
    result = await db.execute(query)
    members = result.scalars().all()

    # Resolve profile photo URLs via media service
    photo_ids = [m.profile_photo_media_id for m in members if m.profile_photo_media_id]
    url_map = await resolve_media_urls(photo_ids) if photo_ids else {}

    response = {}
    for m in members:
        photo_url = (
            url_map.get(str(m.profile_photo_media_id))
            if m.profile_photo_media_id
            else None
        )
        community_paid_until = (
            m.membership.community_paid_until if m.membership else None
        )
        response[str(m.id)] = MemberBasicResponse(
            id=m.id,
            first_name=m.first_name,
            last_name=m.last_name,
            email=m.email,
            profile_photo_media_id=m.profile_photo_media_id,
            profile_photo_url=photo_url,
            community_paid_until=community_paid_until,
        )
    return response
