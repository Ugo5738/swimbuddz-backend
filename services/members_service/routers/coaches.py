"""Coaches router - coach listing endpoints."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.db.session import get_async_db
from services.members_service.models import CoachProfile, Member
from services.members_service.schemas import MemberResponse
from services.members_service.routers._helpers import member_eager_load_options

router = APIRouter(prefix="/members", tags=["coaches"])


@router.get("/coaches", response_model=List[MemberResponse])
async def list_coaches(
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all active coaches.
    Public endpoint - no authentication required.
    Returns Member objects that have an active CoachProfile.
    """
    query = (
        select(Member)
        .join(CoachProfile)
        .where(CoachProfile.status == "active")
        .options(*member_eager_load_options())
        .order_by(Member.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/coaches/{member_id}", response_model=MemberResponse)
async def get_coach_by_id(
    member_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a single active coach's public profile by member ID.
    Public endpoint - no authentication required.
    """
    query = (
        select(Member)
        .join(CoachProfile)
        .where(Member.id == member_id)
        .where(CoachProfile.status == "active")
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    coach = result.scalar_one_or_none()

    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found or not active")

    return coach
