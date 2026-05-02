"""Internal service-to-service endpoints for members-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from datetime import date, datetime, time, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.media_utils import resolve_media_urls
from libs.db.session import get_async_db
from services.members_service.models import (
    CoachAgreement,
    CoachBankAccount,
    CoachProfile,
    Member,
    MemberMembership,
    MemberProfile,
)

router = APIRouter(prefix="/internal/members", tags=["internal"])


# ---------------------------------------------------------------------------
# Response schemas (keep them slim — only what callers need)
# ---------------------------------------------------------------------------


class MemberBasic(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    community_paid_until: str | None = None
    profile_photo_url: str | None = None


class CoachProfileBasic(BaseModel):
    member_id: str
    status: str
    academy_cohort_stipend: int | None = None
    one_to_one_rate_per_hour: int | None = None
    group_session_rate_per_hour: int | None = None


class CoachBankAccountResponse(BaseModel):
    id: str
    member_id: str
    bank_code: str
    bank_name: str | None = None
    account_number: str
    account_name: str | None = None
    is_verified: bool
    recipient_code: str | None = None


class MemberMembershipResponse(BaseModel):
    member_id: str
    primary_tier: str
    active_tiers: list[str] | None = None
    community_paid_until: str | None = None
    club_paid_until: str | None = None
    academy_paid_until: str | None = None


class BulkMembersRequest(BaseModel):
    ids: List[str]


class EligibleCoachBasic(BaseModel):
    member_id: str
    name: str
    email: str
    grade: str | None = None
    total_coaching_hours: int = 0
    average_feedback_rating: float | None = None


class CoachReadinessData(BaseModel):
    """Extended coach profile data for readiness assessment."""

    profile_id: str
    total_coaching_hours: int = 0
    average_rating: float | None = None
    background_check_status: str | None = None
    has_cpr_training: bool = False
    cpr_expiry_date: Optional[str] = None
    has_active_agreement: bool = False


# ---------------------------------------------------------------------------
# Flywheel / funnel reporting schemas
# ---------------------------------------------------------------------------


_VALID_TIERS = {"community", "club", "academy"}


class JoinedTierMember(BaseModel):
    id: str
    source_joined_at: str
    acquisition_source: str | None = None


class JoinedTierResponse(BaseModel):
    members: List[JoinedTierMember]


class TierHistoryEntry(BaseModel):
    tier: str
    entered_at: str
    exited_at: str | None = None


class TierHistoryResponse(BaseModel):
    entries: List[TierHistoryEntry]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/by-auth/{auth_id}", response_model=MemberBasic)
async def get_member_by_auth_id(
    auth_id: str,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Look up a member by Supabase auth_id."""
    result = await db.execute(select(Member).where(Member.auth_id == auth_id))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Resolve profile photo URL from media service
    photo_url = None
    if member.profile_photo_media_id:
        url_map = await resolve_media_urls([member.profile_photo_media_id])
        photo_url = url_map.get(member.profile_photo_media_id)

    return MemberBasic(
        id=str(member.id),
        first_name=member.first_name,
        last_name=member.last_name,
        email=member.email,
        phone=member.profile.phone if member.profile else None,
        profile_photo_url=photo_url,
    )


@router.get("/active", response_model=List[MemberBasic])
async def get_active_members(
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get all active members (for notifications/communications)."""
    result = await db.execute(select(Member).where(Member.is_active.is_(True)))
    members = result.scalars().all()
    return [
        MemberBasic(
            id=str(m.id),
            first_name=m.first_name,
            last_name=m.last_name,
            email=m.email,
            phone=m.profile.phone if m.profile else None,
        )
        for m in members
    ]


class MemberSearchResult(BaseModel):
    """Slim search result with auth_id for cross-service filtering."""

    id: str
    auth_id: str
    first_name: str
    last_name: str
    email: str


@router.get("/search", response_model=List[MemberSearchResult])
async def search_members(
    q: str = Query(..., min_length=1, description="Search term (name or email)"),
    limit: int = Query(50, ge=1, le=200),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Search members by first name, last name, or email (case-insensitive substring).

    Used by other services (e.g., wallet_service admin) to resolve human-readable
    queries into auth_ids for filtering. Returns up to `limit` matches.
    """
    term = f"%{q.strip()}%"
    result = await db.execute(
        select(Member)
        .where(
            (Member.first_name.ilike(term))
            | (Member.last_name.ilike(term))
            | (Member.email.ilike(term))
        )
        .order_by(Member.last_name.asc(), Member.first_name.asc())
        .limit(limit)
    )
    members = result.scalars().all()
    return [
        MemberSearchResult(
            id=str(m.id),
            auth_id=m.auth_id,
            first_name=m.first_name,
            last_name=m.last_name,
            email=m.email,
        )
        for m in members
    ]


# ---------------------------------------------------------------------------
# Reporting: approved members list
# NOTE: This must be defined BEFORE /{member_id} to avoid route conflict.
# ---------------------------------------------------------------------------


class ApprovedMemberBasic(BaseModel):
    id: str
    auth_id: str
    first_name: str
    last_name: str
    primary_tier: str | None = None


@router.get("/approved-list", response_model=List[ApprovedMemberBasic])
async def get_approved_members_list(
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get all approved members with basic info for reporting.

    Used by the reporting service to iterate over all members for quarterly reports.
    """

    result = await db.execute(
        select(Member)
        .options(selectinload(Member.membership))
        .where(
            Member.approval_status == "approved",
            Member.is_active.is_(True),
        )
    )
    members = result.scalars().all()

    return [
        ApprovedMemberBasic(
            id=str(m.id),
            auth_id=m.auth_id,
            first_name=m.first_name,
            last_name=m.last_name,
            primary_tier=(m.membership.primary_tier if m.membership else None),
        )
        for m in members
    ]


# ---------------------------------------------------------------------------
# Flywheel / funnel reporting endpoints
# NOTE: /joined-tier MUST be defined BEFORE /{member_id} to avoid route conflict.
# ---------------------------------------------------------------------------


def _date_window_to_datetimes(start: date, end: date) -> tuple[datetime, datetime]:
    """Convert inclusive date window to UTC datetime [start_of_day, end_of_day]."""
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end, time.max, tzinfo=timezone.utc)
    return start_dt, end_dt


def _tier_paid_until_column(tier: str):
    """Return the SQLAlchemy column tracking access end for the given tier."""
    return {
        "community": MemberMembership.community_paid_until,
        "club": MemberMembership.club_paid_until,
        "academy": MemberMembership.academy_paid_until,
    }[tier]


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
    return MemberMembershipResponse(
        member_id=str(membership.member_id),
        primary_tier=membership.primary_tier,
        active_tiers=membership.active_tiers,
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
        first_name=member.first_name,
        last_name=member.last_name,
        email=member.email,
        phone=member.profile.phone if member.profile else None,
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
            first_name=m.first_name,
            last_name=m.last_name,
            email=m.email,
            phone=m.profile.phone if m.profile else None,
            community_paid_until=(
                m.membership.community_paid_until.isoformat()
                if m.membership and m.membership.community_paid_until
                else None
            ),
        )
        for m in members
    ]


@router.get("/coaches/{member_id}/profile", response_model=CoachProfileBasic)
async def get_coach_profile(
    member_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Look up a coach profile by member_id."""
    result = await db.execute(
        select(CoachProfile).where(CoachProfile.member_id == member_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Coach profile not found")
    return CoachProfileBasic(
        member_id=str(profile.member_id),
        status=profile.status,
        academy_cohort_stipend=profile.academy_cohort_stipend,
        one_to_one_rate_per_hour=profile.one_to_one_rate_per_hour,
        group_session_rate_per_hour=profile.group_session_rate_per_hour,
    )


@router.get("/{member_id}/bank-account", response_model=CoachBankAccountResponse)
async def get_member_bank_account(
    member_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Look up a coach's bank account by member_id."""
    result = await db.execute(
        select(CoachBankAccount).where(CoachBankAccount.member_id == member_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Bank account not found")
    return CoachBankAccountResponse(
        id=str(account.id),
        member_id=str(account.member_id),
        bank_code=account.bank_code,
        bank_name=account.bank_name,
        account_number=account.account_number,
        account_name=account.account_name,
        is_verified=account.is_verified,
        recipient_code=account.paystack_recipient_code,
    )


@router.get("/coaches/eligible", response_model=List[EligibleCoachBasic])
async def get_eligible_coaches(
    grade_column: str = Query(..., description="Coach profile grade column name"),
    eligible_grades: str = Query(
        ..., description="Comma-separated eligible grade values"
    ),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get eligible coaches filtered by grade column and allowed grades.

    The grade_column must be one of the valid category grade columns on CoachProfile.
    eligible_grades is a comma-separated list like 'grade_1,grade_2,grade_3'.
    """
    allowed_columns = {
        "learn_to_swim_grade",
        "special_populations_grade",
        "institutional_grade",
        "competitive_elite_grade",
        "certifications_grade",
        "specialized_disciplines_grade",
        "adjacent_services_grade",
    }
    if grade_column not in allowed_columns:
        raise HTTPException(
            status_code=400, detail=f"Invalid grade column: {grade_column}"
        )

    grades_list = [g.strip() for g in eligible_grades.split(",") if g.strip()]
    if not grades_list:
        return []

    # Get the column object dynamically
    grade_attr = getattr(CoachProfile, grade_column, None)
    if grade_attr is None:
        raise HTTPException(status_code=400, detail=f"Column not found: {grade_column}")

    # Build query with JOIN
    query = (
        select(
            CoachProfile.member_id,
            (Member.first_name + " " + Member.last_name).label("name"),
            Member.email,
            grade_attr.label("grade"),
            CoachProfile.total_coaching_hours,
            CoachProfile.average_feedback_rating,
        )
        .join(Member, CoachProfile.member_id == Member.id)
        .where(CoachProfile.status == "active")
        .where(grade_attr.in_(grades_list))
        .order_by(
            case(
                (grade_attr == "grade_3", 1),
                (grade_attr == "grade_2", 2),
                (grade_attr == "grade_1", 3),
                else_=4,
            ),
            CoachProfile.average_feedback_rating.desc().nulls_last(),
        )
    )

    result = await db.execute(query)
    rows = result.fetchall()

    return [
        EligibleCoachBasic(
            member_id=str(row.member_id),
            name=row.name or "Unknown",
            email=row.email,
            grade=row.grade,
            total_coaching_hours=row.total_coaching_hours or 0,
            average_feedback_rating=row.average_feedback_rating,
        )
        for row in rows
    ]


@router.get("/coaches/{member_id}/readiness", response_model=CoachReadinessData)
async def get_coach_readiness_data(
    member_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get extended coach profile data for readiness assessment.

    Returns profile fields + whether an active agreement exists.
    """
    result = await db.execute(
        select(CoachProfile).where(CoachProfile.member_id == member_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Coach profile not found")

    # Check for active agreement
    agreement_result = await db.execute(
        select(CoachAgreement.id)
        .where(
            CoachAgreement.coach_profile_id == profile.id,
            CoachAgreement.is_active.is_(True),
        )
        .limit(1)
    )
    has_agreement = agreement_result.first() is not None

    return CoachReadinessData(
        profile_id=str(profile.id),
        total_coaching_hours=profile.total_coaching_hours or 0,
        average_rating=profile.average_rating,
        background_check_status=profile.background_check_status,
        has_cpr_training=profile.has_cpr_training or False,
        cpr_expiry_date=(
            profile.cpr_expiry_date.isoformat() if profile.cpr_expiry_date else None
        ),
        has_active_agreement=has_agreement,
    )
