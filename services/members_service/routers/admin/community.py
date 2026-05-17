"""Community-tier admin activate/extend endpoints."""

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from libs.common.datetime_utils import utc_now
from services.members_service.models import Member, MemberMembership
from services.members_service.routers._helpers import member_eager_load_options
from services.members_service.schemas import (
    ActivateCommunityRequest,
    ExtendCommunityRequest,
    MemberResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ._shared import _apply_wallet_paid_activation_side_effects

router = APIRouter()


@router.post("/by-auth/{auth_id}/community/activate", response_model=MemberResponse)
async def admin_activate_community_membership_by_auth(
    auth_id: str,
    payload: ActivateCommunityRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply Community entitlement for a member (admin/service use)."""
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    now = utc_now()

    if not member.membership:
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    previous_paid_until = member.membership.community_paid_until
    base = (
        member.membership.community_paid_until
        if member.membership.community_paid_until
        and member.membership.community_paid_until > now
        else now
    )
    member.membership.community_paid_until = base + relativedelta(years=payload.years)

    if not member.membership.active_tiers:
        member.membership.active_tiers = ["community"]
    if not member.membership.primary_tier:
        member.membership.primary_tier = "community"

    db.add(member)
    await db.commit()
    await db.refresh(member)
    await _apply_wallet_paid_activation_side_effects(
        member,
        first_paid_community_activation=previous_paid_until is None,
    )

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.post("/by-auth/{auth_id}/community/extend", response_model=MemberResponse)
async def admin_extend_community_membership_by_auth(
    auth_id: str,
    payload: ExtendCommunityRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Extend Community membership by months (for stacking with Club)."""
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    now = utc_now()

    if not member.membership:
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    # Extend from current expiry or from now if already expired
    base = (
        member.membership.community_paid_until
        if member.membership.community_paid_until
        and member.membership.community_paid_until > now
        else now
    )
    member.membership.community_paid_until = base + relativedelta(months=payload.months)

    if not member.membership.active_tiers:
        member.membership.active_tiers = ["community"]
    if not member.membership.primary_tier:
        member.membership.primary_tier = "community"

    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()
