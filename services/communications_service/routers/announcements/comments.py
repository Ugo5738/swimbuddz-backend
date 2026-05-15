"""Announcement comment endpoints."""

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
    "/{announcement_id}/comments",
    response_model=AnnouncementCommentResponse,
    status_code=201,
)
async def create_announcement_comment(
    announcement_id: uuid.UUID,
    comment_data: CommentCreate,
    # TODO: Get member_id from authentication
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a comment to an announcement."""
    # Verify announcement exists
    announcement_query = select(Announcement).where(Announcement.id == announcement_id)
    announcement_result = await db.execute(announcement_query)
    announcement = announcement_result.scalar_one_or_none()

    if not announcement:
        raise HTTPException(status_code=404, detail="Announcement not found")

    comment = AnnouncementComment(
        announcement_id=announcement_id,
        member_id=member_id,
        content=comment_data.content,
    )

    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    return AnnouncementCommentResponse.model_validate(comment)

@router.get(
    "/{announcement_id}/comments", response_model=List[AnnouncementCommentResponse]
)
async def list_announcement_comments(
    announcement_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """List all comments for an announcement."""
    query = (
        select(AnnouncementComment)
        .where(AnnouncementComment.announcement_id == announcement_id)
        .order_by(AnnouncementComment.created_at.asc())
    )

    result = await db.execute(query)
    comments_list = result.scalars().all()

    # Resolve member names via HTTP to members service
    member_ids = [c.member_id for c in comments_list]
    member_map = await resolve_members_basic(member_ids) if member_ids else {}

    comments = []
    for comment in comments_list:
        resp = AnnouncementCommentResponse.model_validate(comment)
        info = member_map.get(str(comment.member_id))
        resp.member_name = info.full_name if info else None
        comments.append(resp)

    return comments
