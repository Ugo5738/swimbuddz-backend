"""Admin: grant + list rewards."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from services.volunteer_service.models import VolunteerReward
from services.volunteer_service.schemas import (
    VolunteerRewardCreate,
    VolunteerRewardResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.post("/rewards", response_model=VolunteerRewardResponse, status_code=201)
async def grant_reward(
    data: VolunteerRewardCreate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    _admin = await get_member_by_auth_id(admin.user_id, calling_service="volunteer")
    admin_member_id = uuid.UUID(_admin["id"]) if _admin else None
    reward = VolunteerReward(
        **data.model_dump(),
        granted_by=admin_member_id,
    )
    db.add(reward)
    await db.commit()
    await db.refresh(reward)
    return reward


@router.get("/rewards/all", response_model=list[VolunteerRewardResponse])
async def list_all_rewards(
    admin: Annotated[AuthUser, Depends(require_admin)],
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
):
    rows = (
        (
            await db.execute(
                select(VolunteerReward)
                .order_by(VolunteerReward.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return rows
