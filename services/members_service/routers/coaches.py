"""Coaches router - coach listing endpoints."""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import CoachProfile, Member
from services.members_service.schemas import MemberResponse
from services.members_service.routers._helpers import member_eager_load_options

router = APIRouter(prefix="/members", tags=["coaches"])


@router.get("/coaches", response_model=List[MemberResponse])
async def list_coaches(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all active coaches.
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
