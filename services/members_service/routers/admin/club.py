"""Club-tier admin activate/extend endpoints."""

from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.members_service.models import Member, MemberMembership
from services.members_service.routers._helpers import member_eager_load_options
from services.members_service.schemas import (
    ActivateClubRequest,
    ExtendClubRequest,
    MemberResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)
router = APIRouter()


@router.post("/by-auth/{auth_id}/club/extend", response_model=MemberResponse)
async def admin_extend_club_membership_by_auth(
    auth_id: str,
    payload: ExtendClubRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Extend club membership by N months without eligibility checks.

    Intended for service-to-service grants such as the free 1-month
    post-academy club access bridge (see PRICING_STRATEGY.md). Skips the
    readiness/requested-tier gates that ``/club/activate`` enforces because
    the caller is the system, not the member self-upgrading.

    The new ``club_paid_until`` becomes ``max(current, anchor) + months`` where
    ``anchor = payload.from_date or now``. Idempotent: if club_paid_until is
    already at or past the computed target, no change is made.
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

    now = datetime.now(timezone.utc)

    if not member.membership:
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    anchor = payload.from_date or now
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)

    current_until = member.membership.club_paid_until
    base = current_until if current_until and current_until > anchor else anchor
    new_until = base + relativedelta(months=payload.months)

    # Idempotency: don't shrink, don't no-op an already-covered period.
    if current_until is None or new_until > current_until:
        member.membership.club_paid_until = new_until
        if payload.reason:
            logger.info(
                "club/extend granted: member=%s months=%s reason=%s new_until=%s",
                member.id,
                payload.months,
                payload.reason,
                new_until.isoformat(),
            )

    # Ensure {club, community} are in active_tiers since we just paid for them.
    tier_priority = {"academy": 3, "club": 2, "community": 1}
    updated_tiers = set(member.membership.active_tiers or [])
    updated_tiers.update({"club", "community"})
    member.membership.active_tiers = sorted(
        [t for t in updated_tiers if t in tier_priority],
        key=lambda t: tier_priority[t],
        reverse=True,
    )
    if (
        not member.membership.primary_tier
        or tier_priority.get(member.membership.primary_tier, 0) < tier_priority["club"]
    ):
        member.membership.primary_tier = (
            "academy" if "academy" in member.membership.active_tiers else "club"
        )

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


@router.post("/by-auth/{auth_id}/club/activate", response_model=MemberResponse)
async def admin_activate_club_membership_by_auth(
    auth_id: str,
    payload: ActivateClubRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply Club entitlement for a member (admin/service use)."""
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

    now = datetime.now(timezone.utc)

    if not member.membership:
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    # Skip community check if explicitly requested (for bundle activations where community was just activated)
    if not payload.skip_community_check:
        if not (
            member.membership.community_paid_until
            and member.membership.community_paid_until > now
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Community membership is not active for this member",
            )

    approved_tiers = set(member.membership.active_tiers or [])
    requested_tiers = set(member.membership.requested_tiers or [])
    club_approved = "club" in approved_tiers or "academy" in approved_tiers
    club_requested = "club" in requested_tiers or "academy" in requested_tiers

    ec = member.emergency_contact
    av = member.availability
    readiness_complete = bool(
        ec
        and ec.name
        and ec.contact_relationship
        and ec.phone
        and av
        and av.preferred_locations
        and len(av.preferred_locations) > 0
        and av.preferred_times
        and len(av.preferred_times) > 0
        and av.available_days
        and len(av.available_days) > 0
    )

    if not club_approved:
        if not club_requested:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Club upgrade not requested",
            )
        if not readiness_complete:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Club readiness is incomplete",
            )

    tier_priority = {"academy": 3, "club": 2, "community": 1}

    base = (
        member.membership.club_paid_until
        if member.membership.club_paid_until and member.membership.club_paid_until > now
        else now
    )
    member.membership.club_paid_until = base + relativedelta(months=payload.months)

    # Policy (founder-confirmed May 2026, see docs/club/PRICING_STRATEGY.md):
    # club purchase extends community_paid_until to max(current, NOW + 1 year).
    # Community is the persistent baseline — never shorten, always at least a
    # year out from this club purchase so members on a swimming break stay
    # in the network. Skipped for bundles since the caller already activated
    # community via /community/activate (years param).
    if not payload.skip_community_check:
        one_year_out = now + relativedelta(years=1)
        current_community = member.membership.community_paid_until
        if current_community is None or current_community < one_year_out:
            member.membership.community_paid_until = one_year_out

    updated_tiers = set(approved_tiers)
    updated_tiers.update({"club", "community"})

    if not club_approved:
        if member.membership.requested_tiers:
            remaining_requests = [
                tier
                for tier in member.membership.requested_tiers
                if tier not in {"club", "community"}
            ]
            member.membership.requested_tiers = remaining_requests or None
    elif member.membership.requested_tiers:
        remaining_requests = [
            tier
            for tier in member.membership.requested_tiers
            if tier not in {"club", "academy", "community"}
        ]
        member.membership.requested_tiers = remaining_requests or None

    sorted_tiers = sorted(
        [tier for tier in updated_tiers if tier in tier_priority],
        key=lambda tier: tier_priority[tier],
        reverse=True,
    )
    if sorted_tiers:
        member.membership.active_tiers = sorted_tiers
        current_priority = tier_priority.get(member.membership.primary_tier or "", 0)
        top_priority = tier_priority.get(sorted_tiers[0], 0)
        if top_priority > current_priority:
            member.membership.primary_tier = sorted_tiers[0]

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
