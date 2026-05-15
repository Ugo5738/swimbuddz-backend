"""Read tracking endpoints."""

"""Communications announcements router: announcements, read tracking, comments."""

import uuid
from datetime import datetime, timedelta, timezone
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

router = APIRouter()

@router.post(
    "/{announcement_id}/read", response_model=AnnouncementReadResponse, status_code=201
)
async def mark_announcement_read(
    announcement_id: uuid.UUID,
    read_data: AnnouncementReadCreate,
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Mark an announcement as read by a member.
    If already read, updates acknowledged status if provided.
    """
    # Verify announcement exists
    announcement_query = select(Announcement).where(Announcement.id == announcement_id)
    result = await db.execute(announcement_query)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Announcement not found")

    # Check if already read
    existing_query = select(AnnouncementRead).where(
        AnnouncementRead.announcement_id == announcement_id,
        AnnouncementRead.member_id == member_id,
    )
    existing_result = await db.execute(existing_query)
    existing = existing_result.scalar_one_or_none()

    if existing:
        # Update acknowledged status if provided
        if read_data.acknowledged and not existing.acknowledged:
            existing.acknowledged = True
            existing.acknowledged_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(existing)
        return existing

    # Create new read record
    read_record = AnnouncementRead(
        announcement_id=announcement_id,
        member_id=member_id,
        acknowledged=read_data.acknowledged,
        acknowledged_at=datetime.now(timezone.utc) if read_data.acknowledged else None,
    )
    db.add(read_record)
    await db.commit()
    await db.refresh(read_record)
    return read_record

@router.get("/{announcement_id}/read-status", response_model=AnnouncementReadResponse)
async def get_announcement_read_status(
    announcement_id: uuid.UUID,
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get read status of an announcement for a specific member.
    """
    query = select(AnnouncementRead).where(
        AnnouncementRead.announcement_id == announcement_id,
        AnnouncementRead.member_id == member_id,
    )
    result = await db.execute(query)
    read_record = result.scalar_one_or_none()

    if not read_record:
        raise HTTPException(status_code=404, detail="Read status not found")

    return read_record

@router.get("/{announcement_id}/read-stats")
async def get_announcement_read_stats(
    announcement_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get read/acknowledged statistics for an announcement (Admin only).
    """
    read_count_query = select(func.count(AnnouncementRead.id)).where(
        AnnouncementRead.announcement_id == announcement_id
    )
    acknowledged_count_query = select(func.count(AnnouncementRead.id)).where(
        AnnouncementRead.announcement_id == announcement_id,
        AnnouncementRead.acknowledged.is_(True),
    )

    read_result = await db.execute(read_count_query)
    ack_result = await db.execute(acknowledged_count_query)

    return {
        "announcement_id": announcement_id,
        "read_count": read_result.scalar_one() or 0,
        "acknowledged_count": ack_result.scalar_one() or 0,
    }
