"""Academy-tier admin activate/expire endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import Member, MemberMembership
from services.members_service.routers._helpers import member_eager_load_options
from services.members_service.schemas import ActivateAcademyRequest, MemberResponse
from services.members_service.services.member_service import normalize_member_tiers
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.post("/by-auth/{auth_id}/academy/expire", response_model=MemberResponse)
async def admin_expire_academy_membership_by_auth(
    auth_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Set academy_paid_until to NOW, effectively expiring academy access.

    Used by academy_service after a member's withdrawal when they have no
    remaining ENROLLED cohorts. Subsequent reads via /members/me or the
    internal membership endpoint will strip "academy" from active_tiers
    via normalize_member_tiers since the date is no longer in the future.
    """
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

    if not member.membership:
        # Nothing to expire — return member unchanged.
        return member

    now = datetime.now(timezone.utc)
    member.membership.academy_paid_until = now

    # Recompute active_tiers + primary_tier from the new state
    new_primary, new_tiers, changed = normalize_member_tiers(
        current_tier=member.membership.primary_tier,
        current_tiers=member.membership.active_tiers,
        community_paid_until=member.membership.community_paid_until,
        club_paid_until=member.membership.club_paid_until,
        academy_paid_until=member.membership.academy_paid_until,
    )
    if changed:
        member.membership.primary_tier = new_primary
        member.membership.active_tiers = new_tiers

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


@router.post("/by-auth/{auth_id}/academy/activate", response_model=MemberResponse)
async def admin_activate_academy_membership_by_auth(
    auth_id: str,
    payload: ActivateAcademyRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Set (or extend) the academy tier for a member until cohort_end_date.

    A member may be enrolled in multiple simultaneous cohorts ending at different
    dates. This endpoint always keeps academy_paid_until at the *latest* cohort
    end date seen, so access is never prematurely revoked.
    Called by payments_service after a successful academy cohort payment.
    """
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

    if not member.membership:
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    # Ensure the end date is timezone-aware
    new_end = payload.cohort_end_date
    if new_end.tzinfo is None:
        new_end = new_end.replace(tzinfo=timezone.utc)

    # Keep the later of the current value and the supplied cohort end date,
    # so multiple overlapping enrollments don't truncate each other.
    current_until = member.membership.academy_paid_until
    if current_until is None or new_end > current_until:
        member.membership.academy_paid_until = new_end

    # Update active_tiers to include academy (and implied club + community)
    tier_priority = {"academy": 3, "club": 2, "community": 1}
    updated_tiers = set(member.membership.active_tiers or [])
    updated_tiers.update({"academy", "club", "community"})
    sorted_tiers = sorted(
        [t for t in updated_tiers if t in tier_priority],
        key=lambda t: tier_priority[t],
        reverse=True,
    )
    member.membership.active_tiers = sorted_tiers

    current_priority = tier_priority.get(member.membership.primary_tier or "", 0)
    if tier_priority.get("academy", 0) > current_priority:
        member.membership.primary_tier = "academy"

    db.add(member)
    await db.commit()

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()
