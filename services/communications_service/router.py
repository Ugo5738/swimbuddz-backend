from typing import List
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.communications_service.models import Announcement
from services.communications_service.schemas import AnnouncementResponse, AnnouncementCreate

router = APIRouter(prefix="/announcements", tags=["announcements"])


@router.get("/", response_model=List[AnnouncementResponse])
async def list_announcements(
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all announcements, newest first.
    """
    query = select(Announcement).order_by(
        Announcement.is_pinned.desc(),
        Announcement.published_at.desc()
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{announcement_id}", response_model=AnnouncementResponse)
async def get_announcement(
    announcement_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get details of a specific announcement.
    """
    query = select(Announcement).where(Announcement.id == announcement_id)
    result = await db.execute(query)
    announcement = result.scalar_one_or_none()
    
    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Announcement not found",
        )
    return announcement


@router.post("/", response_model=AnnouncementResponse, status_code=status.HTTP_201_CREATED)
async def create_announcement(
    announcement_in: AnnouncementCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a new announcement (Admin only).
    """
    announcement = Announcement(**announcement_in.model_dump())
    db.add(announcement)
    await db.commit()
    await db.refresh(announcement)
    return announcement
