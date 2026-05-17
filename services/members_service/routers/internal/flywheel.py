"""Flywheel / funnel reporting endpoint.

`/joined-tier` is static-path; must be registered before any
`/{member_id}` dynamic route in the aggregator.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import Member, MemberMembership, MemberProfile
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ._helpers import _VALID_TIERS, _date_window_to_datetimes, _tier_paid_until_column
from ._schemas import JoinedTierMember, JoinedTierResponse

router = APIRouter()


@router.get("/joined-tier", response_model=JoinedTierResponse)
async def get_members_who_joined_tier(
    tier: str = Query(..., description="One of: community, club, academy"),
    from_: date = Query(..., alias="from", description="ISO date (inclusive)"),
    to: date = Query(..., description="ISO date (inclusive)"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Members who entered ``tier`` in the [from, to] window.

    Used by reporting_service.tasks.flywheel for funnel-conversion snapshots.
    Decision: no tier-transition audit table exists, so we use the simplest
    proxy. For ``community`` we treat ``Member.created_at`` as the entry
    signal when ``primary_tier == 'community'`` OR the member has ``community``
    in ``active_tiers``. For ``club``/``academy`` we approximate entry from
    ``MemberMembership.{tier}_paid_until`` falling within the window — i.e.
    a non-null paid_until that landed in [from, to] indicates the member
    crossed into that tier during the window. This is best-effort and should
    be replaced with an explicit tier-transition audit log in future.
    """
    if tier not in _VALID_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier: {tier!r}. Must be one of {_VALID_TIERS}",
        )
    if from_ > to:
        raise HTTPException(status_code=400, detail="`from` must be <= `to`")

    start_dt, end_dt = _date_window_to_datetimes(from_, to)

    if tier == "community":
        # Proxy: community entry == member account creation, when their
        # primary tier is community (or community is in their active tiers).
        stmt = (
            select(Member.id, Member.created_at, MemberProfile.acquisition_source)
            .join(MemberMembership, MemberMembership.member_id == Member.id)
            .outerjoin(MemberProfile, MemberProfile.member_id == Member.id)
            .where(
                Member.created_at >= start_dt,
                Member.created_at <= end_dt,
                or_(
                    MemberMembership.primary_tier == "community",
                    MemberMembership.active_tiers.any("community"),
                ),
            )
        )
    else:
        paid_until_col = _tier_paid_until_column(tier)
        stmt = (
            select(Member.id, paid_until_col, MemberProfile.acquisition_source)
            .join(MemberMembership, MemberMembership.member_id == Member.id)
            .outerjoin(MemberProfile, MemberProfile.member_id == Member.id)
            .where(
                paid_until_col.is_not(None),
                paid_until_col >= start_dt,
                paid_until_col <= end_dt,
            )
        )

    result = await db.execute(stmt)
    rows = result.all()

    members = [
        JoinedTierMember(
            id=str(row[0]),
            source_joined_at=row[1].isoformat() if row[1] else "",
            acquisition_source=(row[2].value if row[2] is not None else None),
        )
        for row in rows
        if row[1] is not None
    ]
    return JoinedTierResponse(members=members)
