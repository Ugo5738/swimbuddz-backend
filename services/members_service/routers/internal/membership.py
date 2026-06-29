"""Per-member membership + tier-history + bulk lookup endpoints.

These all use the `/{member_id}` dynamic-segment pattern (or POST `/bulk`),
so this sub-router must be included LAST in the aggregator, after every
sub-router that owns a literal-first-segment route at the same depth
(e.g. `/active`, `/search`, `/birthdays-today`, `/joined-tier`).
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import Member, MemberMembership
from services.members_service.services.member_service import normalize_member_tiers
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ._schemas import (
    BulkMembersRequest,
    MemberBasic,
    MemberMembershipResponse,
    TierHistoryEntry,
    TierHistoryResponse,
)

router = APIRouter()


@router.get("/{member_id}/membership", response_model=MemberMembershipResponse)
async def get_member_membership(
    member_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Look up a member's membership tier and billing info."""
    result = await db.execute(
        select(MemberMembership).where(MemberMembership.member_id == member_id)
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")

    # Strip expired tiers on read — stored column is a cache that may have
    # drifted past expiry dates. Write back if it changed so future reads
    # (and other services reading the column directly) see fresh values.
    new_primary, new_tiers, changed = normalize_member_tiers(
        current_tier=membership.primary_tier,
        current_tiers=membership.active_tiers,
        community_paid_until=membership.community_paid_until,
        club_paid_until=membership.club_paid_until,
        academy_paid_until=membership.academy_paid_until,
    )
    if changed:
        membership.primary_tier = new_primary
        membership.active_tiers = new_tiers
        await db.commit()

    return MemberMembershipResponse(
        member_id=str(membership.member_id),
        primary_tier=new_primary,
        active_tiers=new_tiers,
        community_paid_until=(
            membership.community_paid_until.isoformat()
            if membership.community_paid_until
            else None
        ),
        club_paid_until=(
            membership.club_paid_until.isoformat()
            if membership.club_paid_until
            else None
        ),
        academy_paid_until=(
            membership.academy_paid_until.isoformat()
            if membership.academy_paid_until
            else None
        ),
    )


@router.get("/{member_id}/tier-history", response_model=TierHistoryResponse)
async def get_member_tier_history(
    member_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Best-effort tier entry/exit history for a single member.

    Used by reporting_service.tasks.flywheel to verify whether a member
    crossed into a target tier within an observation window.

    Decision: no tier-transition audit table exists, so we derive entries
    from the current MemberMembership state:
      - community: entered_at = Member.created_at (community is the default
        starting tier); exited_at = community_paid_until if it lies in the
        past, else None.
      - club / academy: entry exists only if {tier}_paid_until is non-null;
        entered_at is approximated as Member.created_at (we don't track the
        actual upgrade timestamp), exited_at = {tier}_paid_until.
    Entries are returned only for tiers the member has a signal for. This
    is best-effort and should be replaced when an audit log is introduced.
    """
    member_result = await db.execute(
        select(Member, MemberMembership)
        .outerjoin(MemberMembership, MemberMembership.member_id == Member.id)
        .where(Member.id == member_id)
    )
    row = member_result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Member not found")

    member, membership = row[0], row[1]
    entries: list[TierHistoryEntry] = []

    if membership is None:
        # No membership row — return only the implicit community entry.
        entries.append(
            TierHistoryEntry(
                tier="community",
                entered_at=member.created_at.isoformat(),
                exited_at=None,
            )
        )
        return TierHistoryResponse(entries=entries)

    active = set(membership.active_tiers or [])
    if (
        membership.primary_tier == "community"
        or "community" in active
        or membership.community_paid_until is not None
    ):
        entries.append(
            TierHistoryEntry(
                tier="community",
                entered_at=member.created_at.isoformat(),
                exited_at=(
                    membership.community_paid_until.isoformat()
                    if membership.community_paid_until
                    else None
                ),
            )
        )

    if membership.club_paid_until is not None or "club" in active:
        entries.append(
            TierHistoryEntry(
                tier="club",
                entered_at=member.created_at.isoformat(),
                exited_at=(
                    membership.club_paid_until.isoformat()
                    if membership.club_paid_until
                    else None
                ),
            )
        )

    if membership.academy_paid_until is not None or "academy" in active:
        entries.append(
            TierHistoryEntry(
                tier="academy",
                entered_at=member.created_at.isoformat(),
                exited_at=(
                    membership.academy_paid_until.isoformat()
                    if membership.academy_paid_until
                    else None
                ),
            )
        )

    return TierHistoryResponse(entries=entries)


@router.get("/{member_id}", response_model=MemberBasic)
async def get_member_by_id(
    member_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Look up a member by ID."""
    result = await db.execute(select(Member).where(Member.id == member_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    return MemberBasic(
        id=str(member.id),
        auth_id=member.auth_id,
        first_name=member.first_name,
        last_name=member.last_name,
        email=member.email,
        phone=member.profile.phone if member.profile else None,
        date_of_birth=(
            member.profile.date_of_birth.isoformat()
            if member.profile and member.profile.date_of_birth
            else None
        ),
    )


@router.post("/bulk", response_model=List[MemberBasic])
async def get_members_bulk(
    body: BulkMembersRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Bulk-lookup members by IDs."""
    if not body.ids:
        return []
    uuids = [uuid.UUID(mid) for mid in body.ids]

    result = await db.execute(
        select(Member)
        .where(Member.id.in_(uuids))
        .options(selectinload(Member.membership))
    )
    members = result.scalars().all()
    return [
        MemberBasic(
            id=str(m.id),
            auth_id=m.auth_id,
            first_name=m.first_name,
            last_name=m.last_name,
            email=m.email,
            phone=m.profile.phone if m.profile else None,
            date_of_birth=(
                m.profile.date_of_birth.isoformat()
                if m.profile and m.profile.date_of_birth
                else None
            ),
            community_paid_until=(
                m.membership.community_paid_until.isoformat()
                if m.membership and m.membership.community_paid_until
                else None
            ),
        )
        for m in members
    ]
