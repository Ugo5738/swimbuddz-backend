"""Member-facing reward listing + redemption."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_auth_id
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.volunteer_service.models import VolunteerReward
from services.volunteer_service.schemas import VolunteerRewardResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/rewards/me", response_model=list[VolunteerRewardResponse])
async def my_rewards(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Get my rewards."""
    member = await get_member_by_auth_id(user.user_id, calling_service="volunteer")
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(member["id"])
    rows = (
        (
            await db.execute(
                select(VolunteerReward)
                .where(VolunteerReward.member_id == member_id)
                .order_by(VolunteerReward.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post("/rewards/{reward_id}/redeem", response_model=VolunteerRewardResponse)
async def redeem_reward(
    reward_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Redeem a reward."""
    member = await get_member_by_auth_id(user.user_id, calling_service="volunteer")
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(member["id"])
    reward = (
        await db.execute(
            select(VolunteerReward).where(
                VolunteerReward.id == reward_id,
                VolunteerReward.member_id == member_id,
            )
        )
    ).scalar_one_or_none()
    if not reward:
        raise HTTPException(status_code=404, detail="Reward not found")
    if reward.is_redeemed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reward already redeemed",
        )
    if reward.expires_at and reward.expires_at < utc_now():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Reward has expired",
        )

    reward.is_redeemed = True
    reward.redeemed_at = utc_now()
    await db.commit()
    await db.refresh(reward)
    return reward
