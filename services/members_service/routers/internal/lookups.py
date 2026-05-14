"""Static-path member lookup endpoints.

`/by-auth/{auth_id}`, `/active`, `/search`, `/approved-list` — all must be
registered before any `/{member_id}` dynamic route in the aggregator so
FastAPI doesn't capture the literal segment as a UUID.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.media_utils import resolve_media_urls
from libs.db.session import get_async_db
from services.members_service.models import Member
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ._schemas import (
    ApprovedMemberBasic,
    MemberBasic,
    MemberSearchResult,
)

router = APIRouter()


@router.get("/by-auth/{auth_id}", response_model=MemberBasic)
async def get_member_by_auth_id(
    auth_id: str,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Look up a member by Supabase auth_id."""
    result = await db.execute(select(Member).where(Member.auth_id == auth_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Resolve profile photo URL from media service
    photo_url = None
    if member.profile_photo_media_id:
        url_map = await resolve_media_urls([member.profile_photo_media_id])
        photo_url = url_map.get(member.profile_photo_media_id)

    return MemberBasic(
        id=str(member.id),
        first_name=member.first_name,
        last_name=member.last_name,
        email=member.email,
        phone=member.profile.phone if member.profile else None,
        profile_photo_url=photo_url,
    )


@router.get("/active", response_model=List[MemberBasic])
async def get_active_members(
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get all active members (for notifications/communications)."""
    result = await db.execute(select(Member).where(Member.is_active.is_(True)))
    members = result.scalars().all()
    return [
        MemberBasic(
            id=str(m.id),
            first_name=m.first_name,
            last_name=m.last_name,
            email=m.email,
            phone=m.profile.phone if m.profile else None,
        )
        for m in members
    ]


@router.get("/search", response_model=List[MemberSearchResult])
async def search_members(
    q: str = Query(..., min_length=1, description="Search term (name or email)"),
    limit: int = Query(50, ge=1, le=200),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Search members by first name, last name, or email (case-insensitive substring).

    Used by other services (e.g., wallet_service admin) to resolve human-readable
    queries into auth_ids for filtering. Returns up to `limit` matches.
    """
    term = f"%{q.strip()}%"
    result = await db.execute(
        select(Member)
        .where(
            (Member.first_name.ilike(term))
            | (Member.last_name.ilike(term))
            | (Member.email.ilike(term))
        )
        .order_by(Member.last_name.asc(), Member.first_name.asc())
        .limit(limit)
    )
    members = result.scalars().all()
    return [
        MemberSearchResult(
            id=str(m.id),
            auth_id=m.auth_id,
            first_name=m.first_name,
            last_name=m.last_name,
            email=m.email,
        )
        for m in members
    ]


@router.get("/approved-list", response_model=List[ApprovedMemberBasic])
async def get_approved_members_list(
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get all approved members with basic info for reporting.

    Used by the reporting service to iterate over all members for quarterly reports.
    """

    result = await db.execute(
        select(Member)
        .options(selectinload(Member.membership))
        .where(
            Member.approval_status == "approved",
            Member.is_active.is_(True),
        )
    )
    members = result.scalars().all()

    return [
        ApprovedMemberBasic(
            id=str(m.id),
            auth_id=m.auth_id,
            first_name=m.first_name,
            last_name=m.last_name,
            primary_tier=(m.membership.primary_tier if m.membership else None),
        )
        for m in members
    ]
