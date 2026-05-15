"""Admin actions on announcement comments."""

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

@router.delete("/members/{member_id}")
async def admin_delete_member_comments(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete member comments in communications service (Admin only).
    """
    content_result = await db.execute(
        delete(ContentComment).where(ContentComment.member_id == member_id)
    )
    announcement_result = await db.execute(
        delete(AnnouncementComment).where(AnnouncementComment.member_id == member_id)
    )
    await db.commit()
    return {
        "deleted_content_comments": content_result.rowcount or 0,
        "deleted_announcement_comments": announcement_result.rowcount or 0,
    }
