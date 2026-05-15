"""Announcement CRUD + stats + unread-count."""

"""Communications announcements router: announcements, read tracking, comments."""

import uuid
from datetime import timedelta
from typing import List, Optional, Set

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_optional_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.member_utils import resolve_members_basic
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.communications_service.models import (
    Announcement,
    AnnouncementAudience,
    AnnouncementCategory,
    AnnouncementComment,
    AnnouncementRead,
    AnnouncementStatus,
    ContentComment,
    NotificationPreferences,
)
from services.communications_service.schemas import (
    AnnouncementCommentResponse,
    AnnouncementCreate,
    AnnouncementReadCreate,
    AnnouncementReadResponse,
    AnnouncementResponse,
    AnnouncementUpdate,
    CommentCreate,
)
from services.communications_service.templates.messaging import send_message_email

settings = get_settings()
logger = get_logger(__name__)
from ._helpers import (
    _default_expiry,
    _default_notification_flags,
    _get_allowed_audiences,
    _is_admin,
    _send_announcement_emails,
)

router = APIRouter()


@router.get("/", response_model=List[AnnouncementResponse])
async def list_announcements(
    request: Request,
    include_all: bool = Query(
        False, description="Include drafts/archived/expired (admin only)"
    ),
    limit: Optional[int] = Query(None, ge=1, le=100, description="Max items to return"),
    unread_only: bool = Query(
        False, description="Only return unread (requires member_id)"
    ),
    member_id: Optional[uuid.UUID] = Query(
        None, description="Member ID for unread filtering"
    ),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List announcements, newest first. Supports limit and unread-only filtering.
    """
    if include_all and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )

    query = select(Announcement)
    if not include_all:
        now = utc_now()
        allowed_audiences = await _get_allowed_audiences(
            request.headers.get("authorization")
        )
        query = query.where(
            Announcement.status == AnnouncementStatus.PUBLISHED,
            or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
            Announcement.audience.in_(allowed_audiences),
        )

    if unread_only and member_id:
        read_ids = (
            select(AnnouncementRead.announcement_id)
            .where(AnnouncementRead.member_id == member_id)
            .scalar_subquery()
        )
        query = query.where(Announcement.id.notin_(read_ids))

    query = query.order_by(
        Announcement.is_pinned.desc(), Announcement.published_at.desc()
    )

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats")
async def get_announcement_stats(
    request: Request,
    include_all: bool = Query(
        False, description="Include drafts/archived/expired (admin only)"
    ),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get announcement statistics.
    """
    # Just total count for now
    if include_all and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )

    query = select(func.count(Announcement.id))
    if not include_all:
        now = utc_now()
        allowed_audiences = await _get_allowed_audiences(
            request.headers.get("authorization")
        )
        query = query.where(
            Announcement.status == AnnouncementStatus.PUBLISHED,
            or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
            Announcement.audience.in_(allowed_audiences),
        )
    result = await db.execute(query)
    recent_announcements_count = result.scalar_one() or 0

    return {"recent_announcements_count": recent_announcements_count}


@router.get("/unread-count")
async def get_unread_count(
    request: Request,
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """Return the number of published, non-expired announcements the member has not read."""
    now = utc_now()
    allowed_audiences = await _get_allowed_audiences(
        request.headers.get("authorization")
    )

    # Subquery: announcement IDs this member has read
    read_ids = (
        select(AnnouncementRead.announcement_id)
        .where(AnnouncementRead.member_id == member_id)
        .scalar_subquery()
    )

    query = select(func.count(Announcement.id)).where(
        Announcement.status == AnnouncementStatus.PUBLISHED,
        or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
        Announcement.audience.in_(allowed_audiences),
        Announcement.id.notin_(read_ids),
    )
    result = await db.execute(query)
    return {"unread_count": result.scalar_one() or 0}


@router.get("/{announcement_id}", response_model=AnnouncementResponse)
async def get_announcement(
    announcement_id: uuid.UUID,
    request: Request,
    include_all: bool = Query(
        False, description="Include drafts/archived/expired (admin only)"
    ),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get details of a specific announcement.
    """
    if include_all and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )

    query = select(Announcement).where(Announcement.id == announcement_id)
    if not include_all:
        now = utc_now()
        allowed_audiences = await _get_allowed_audiences(
            request.headers.get("authorization")
        )
        query = query.where(
            Announcement.status == AnnouncementStatus.PUBLISHED,
            or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
            Announcement.audience.in_(allowed_audiences),
        )
    result = await db.execute(query)
    announcement = result.scalar_one_or_none()

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Announcement not found",
        )
    return announcement


@router.post("/", response_model=AnnouncementResponse, status_code=201)
async def create_announcement(
    announcement_data: AnnouncementCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a new announcement (Admin only).
    """
    payload = announcement_data.model_dump()
    status_value = payload.get("status", AnnouncementStatus.PUBLISHED)

    published_at = payload.get("published_at")
    if status_value == AnnouncementStatus.PUBLISHED and not published_at:
        published_at = utc_now()
    elif status_value == AnnouncementStatus.DRAFT:
        published_at = None

    expires_at = payload.get("expires_at")
    if not expires_at and status_value == AnnouncementStatus.PUBLISHED:
        expires_at = _default_expiry(
            payload.get("category", AnnouncementCategory.GENERAL)
        )

    notify_email = payload.get("notify_email")
    notify_push = payload.get("notify_push")
    if notify_email is None or notify_push is None:
        default_email, default_push = _default_notification_flags(
            payload.get("category", AnnouncementCategory.GENERAL)
        )
        if notify_email is None:
            notify_email = default_email
        if notify_push is None:
            notify_push = default_push

    announcement = Announcement(
        **payload,
        published_at=published_at,
        expires_at=expires_at,
        notify_email=notify_email,
        notify_push=notify_push,
    )
    db.add(announcement)
    await db.commit()
    await db.refresh(announcement)
    await _send_announcement_emails(announcement, db)
    return announcement


@router.patch("/{announcement_id}", response_model=AnnouncementResponse)
async def update_announcement(
    announcement_id: uuid.UUID,
    announcement_data: AnnouncementUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update an announcement (Admin only).
    """
    query = select(Announcement).where(Announcement.id == announcement_id)
    result = await db.execute(query)
    announcement = result.scalar_one_or_none()

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Announcement not found",
        )

    update_data = announcement_data.model_dump(exclude_unset=True)
    if "status" in update_data:
        if (
            update_data["status"] == AnnouncementStatus.PUBLISHED
            and not announcement.published_at
        ):
            announcement.published_at = utc_now()
        elif update_data["status"] == AnnouncementStatus.DRAFT:
            announcement.published_at = None
        if (
            update_data["status"] == AnnouncementStatus.PUBLISHED
            and "expires_at" not in update_data
            and not announcement.expires_at
        ):
            category_value = update_data.get("category", announcement.category)
            announcement.expires_at = _default_expiry(category_value)

    for field, value in update_data.items():
        setattr(announcement, field, value)

    await db.commit()
    await db.refresh(announcement)
    if (
        "status" in update_data
        and update_data["status"] == AnnouncementStatus.PUBLISHED
    ):
        await _send_announcement_emails(announcement, db)
    return announcement


@router.delete("/{announcement_id}", status_code=204)
async def delete_announcement(
    announcement_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete an announcement (Admin only).
    """
    query = select(Announcement).where(Announcement.id == announcement_id)
    result = await db.execute(query)
    announcement = result.scalar_one_or_none()

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Announcement not found",
        )

    # Delete comments and reads first
    await db.execute(
        delete(AnnouncementComment).where(
            AnnouncementComment.announcement_id == announcement_id
        )
    )
    await db.execute(
        delete(AnnouncementRead).where(
            AnnouncementRead.announcement_id == announcement_id
        )
    )
    await db.delete(announcement)
    await db.commit()
