"""Partial-update admin endpoint for membership payment state."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from pydantic import BaseModel
from services.members_service.models import Member, MemberMembership
from services.members_service.routers._helpers import member_eager_load_options
from services.members_service.schemas import MemberResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class MembershipPatchRequest(BaseModel):
    """Partial update for membership fields."""

    pending_payment_reference: str | None = None
    pending_payment_tier: Literal["community", "club", "academy"] | None = None
    expected_pending_payment_reference: str | None = None


@router.patch("/by-auth/{auth_id}/membership", response_model=MemberResponse)
async def admin_patch_membership_by_auth(
    auth_id: str,
    payload: MembershipPatchRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Partially update membership fields for a member (admin/service use)."""
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(*member_eager_load_options())
        .with_for_update()
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    if not member.membership:
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    fields = payload.model_dump(exclude_unset=True)
    if "pending_payment_reference" in fields:
        reference = payload.pending_payment_reference
        tier = payload.pending_payment_tier
        if tier:
            pending = dict(member.membership.pending_tier_payments or {})
            expected = payload.expected_pending_payment_reference
            if reference:
                pending[tier] = reference
            elif expected is None or pending.get(tier) == expected:
                pending.pop(tier, None)
            member.membership.pending_tier_payments = pending
            member.membership.pending_payment_reference = next(
                iter(pending.values()), None
            )
        else:
            # Legacy callers may still maintain the resumable reference, but it
            # no longer affects normalized membership status.
            member.membership.pending_payment_reference = reference

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
