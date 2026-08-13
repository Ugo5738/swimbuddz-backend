"""Internal service-to-service endpoints for sessions-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from datetime import datetime, timedelta
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_member_by_auth_id
from libs.common.session_access import denial_message
from libs.db.session import get_async_db
from services.sessions_service.models import (
    BookingChannel,
    GuestPass,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionCoach,
    SessionStatus,
)
from services.sessions_service.schemas import (
    BookingConfirmRequest,
    BulkBookingRequest,
    BulkBookingResponse,
    BundleBookingConfirmRequest,
    BundleBookingConfirmResponse,
    BundleBookingLineResponse,
    BundleBookingReleaseRequest,
    BundleBookingReleaseResponse,
    BundleBookingReserveRequest,
    BundleBookingReserveResponse,
    MemberSessionAccessResponse,
    SessionBookingResponse,
)
from services.sessions_service.services.booking_attendance import (
    sync_booking_attendance,
)
from services.sessions_service.services.booking_capacity import (
    PENDING_TTL_MINUTES,
    assert_booking_capacity,
)
from services.sessions_service.services.session_access import (
    evaluate_session_access_for_member,
    get_member_session_access_payload,
)

router = APIRouter(prefix="/internal/sessions", tags=["internal"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SessionBasic(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    notes: Optional[str] = None
    session_type: str
    status: str
    starts_at: str
    ends_at: str
    pool_id: Optional[str] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    location: Optional[str] = None
    cohort_id: Optional[str] = None
    pod_id: Optional[str] = None
    capacity: int
    # pool_fee is returned in KOBO (integer) for service-to-service use.
    # Wallet-only consumers require pool_fee to be exactly divisible by one Bubble.
    pool_fee: Optional[int] = None
    ride_share_fee: Optional[int] = None
    occupied_slots: int = 0
    confirmed_booking_member_ids: List[str] = Field(default_factory=list)
    coach_member_ids: List[str] = Field(default_factory=list)
    week_number: Optional[int] = None
    lesson_title: Optional[str] = None
    timezone: str = "Africa/Lagos"


class MemberSessionCommitment(BaseModel):
    """A confirmed member commitment joined to its scheduled session."""

    booking_id: str
    session_id: str
    member_id: str
    member_auth_id: str
    title: str
    session_type: str
    session_status: str
    starts_at: str
    ends_at: str
    location_name: Optional[str] = None
    cohort_id: Optional[str] = None
    pod_id: Optional[str] = None
    event_id: Optional[str] = None
    week_number: Optional[int] = None


class NextSessionResponse(BaseModel):
    starts_at: str
    title: str
    location_name: Optional[str] = None


class GenerateCohortSessionsRequest(BaseModel):
    # Half-open window (from_date, to_date]. Typically from_date = the cohort's
    # pre-extension end_date and to_date = the new (post-extension) end_date.
    from_date: datetime
    to_date: datetime


class GenerateCohortSessionsResponse(BaseModel):
    created: int
    skipped: int
    week_numbers: List[int]
    reason: Optional[str] = None


class SessionSummaryBatchRequest(BaseModel):
    session_ids: List[uuid.UUID] = Field(default_factory=list, max_length=200)


class SessionListSummary(BaseModel):
    id: str
    title: str
    session_type: str
    starts_at: str
    location_name: Optional[str] = None
    location: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# NOTE: Static path "/scheduled" must be registered before the
# parameterized "/{session_id}" to avoid route collision (FastAPI
# matches routes in definition order).


@router.get("/scheduled", response_model=List[SessionBasic])
async def get_scheduled_sessions(
    # datetime, not str: binds as timestamptz (str 500s the starts_at comparison)
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    include_completed: bool = False,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get scheduled sessions within a date range."""
    statuses = [SessionStatus.SCHEDULED]
    if include_completed:
        statuses.extend([SessionStatus.IN_PROGRESS, SessionStatus.COMPLETED])
    query = select(Session).where(Session.status.in_(statuses))
    if start_date:
        query = query.where(Session.starts_at >= start_date)
    if end_date:
        query = query.where(Session.starts_at < end_date)
    query = query.order_by(Session.starts_at.asc())
    result = await db.execute(query)
    sessions = result.scalars().all()
    session_ids = [session.id for session in sessions]
    confirmed_by_session: dict[uuid.UUID, list[str]] = {
        session_id: [] for session_id in session_ids
    }
    occupied_by_session: dict[uuid.UUID, int] = {
        session_id: 0 for session_id in session_ids
    }
    coaches_by_session: dict[uuid.UUID, list[str]] = {
        session_id: [] for session_id in session_ids
    }
    if session_ids:
        booking_rows = (
            await db.execute(
                select(
                    SessionBooking.session_id,
                    SessionBooking.member_id,
                    SessionBooking.party_size,
                ).where(
                    SessionBooking.session_id.in_(session_ids),
                    SessionBooking.status == SessionBookingStatus.CONFIRMED,
                )
            )
        ).all()
        for session_id, member_id, party_size in booking_rows:
            confirmed_by_session[session_id].append(str(member_id))
            occupied_by_session[session_id] += int(party_size or 1)

        coach_rows = (
            await db.execute(
                select(SessionCoach.session_id, SessionCoach.coach_id).where(
                    SessionCoach.session_id.in_(session_ids)
                )
            )
        ).all()
        for session_id, coach_id in coach_rows:
            coaches_by_session[session_id].append(str(coach_id))

    return [
        SessionBasic(
            id=str(s.id),
            title=s.title,
            description=s.description,
            notes=s.notes,
            session_type=s.session_type.value,
            status=s.status.value,
            starts_at=s.starts_at.isoformat(),
            ends_at=s.ends_at.isoformat(),
            pool_id=str(s.pool_id) if s.pool_id else None,
            location_name=s.location_name,
            location_address=s.location_address,
            location=s.location.value if s.location else None,
            cohort_id=str(s.cohort_id) if s.cohort_id else None,
            pod_id=str(s.pod_id) if s.pod_id else None,
            capacity=s.capacity,
            pool_fee=s.pool_fee,
            ride_share_fee=s.ride_share_fee,
            occupied_slots=occupied_by_session[s.id],
            confirmed_booking_member_ids=confirmed_by_session[s.id],
            coach_member_ids=coaches_by_session[s.id],
            week_number=s.week_number,
            lesson_title=s.lesson_title,
            timezone=s.timezone,
        )
        for s in sessions
    ]


# ---------------------------------------------------------------------------
# Reporting aggregation
# NOTE: Static path "/range-stats" must be registered before the
# parameterized "/{session_id}" to avoid route collision.
# ---------------------------------------------------------------------------


class SessionRangeStats(BaseModel):
    """Aggregated session stats for a date range."""

    total_sessions: int = 0
    by_type: dict | None = None
    new_members: int = 0  # placeholder — computed elsewhere


class SessionDetailedStats(BaseModel):
    """Extended session stats for quarterly reports."""

    total_sessions: int = 0
    total_pool_hours: float = 0.0
    guest_swimmer_hours: float = 0.0
    by_type: dict | None = None
    most_active_location: str | None = None
    busiest_session_title: str | None = None
    busiest_session_attendance: int = 0
    most_popular_day: str | None = None
    most_popular_time_slot: str | None = None
    session_details: list[dict] | None = None


class CampaignBookingStats(BaseModel):
    campaign_key: str
    total: int = 0
    pending: int = 0
    confirmed: int = 0
    cancelled: int = 0
    expired: int = 0


@router.get("/bookings/campaign-stats", response_model=CampaignBookingStats)
async def get_campaign_booking_stats(
    campaign_key: str = Query(..., min_length=1, max_length=80),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> CampaignBookingStats:
    """Count booking outcomes attributed to a digest or other campaign."""
    rows = (
        await db.execute(
            select(SessionBooking.status, func.count(SessionBooking.id))
            .where(SessionBooking.campaign_key == campaign_key)
            .group_by(SessionBooking.status)
        )
    ).all()
    counts = {
        status.value if hasattr(status, "value") else str(status): int(count)
        for status, count in rows
    }
    return CampaignBookingStats(
        campaign_key=campaign_key,
        total=sum(counts.values()),
        pending=counts.get("pending", 0),
        confirmed=counts.get("confirmed", 0),
        cancelled=counts.get("cancelled", 0),
        expired=counts.get("expired", 0),
    )


@router.get("/range-stats", response_model=SessionRangeStats)
async def get_session_range_stats(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get aggregated session stats within a date range.

    Used by the reporting service for quarterly community stats.
    """
    from collections import Counter

    result = await db.execute(
        select(Session).where(
            Session.starts_at >= date_from,
            Session.starts_at <= date_to,
            Session.status.in_(
                [
                    SessionStatus.SCHEDULED,
                    SessionStatus.COMPLETED,
                ]
            ),
        )
    )
    sessions = result.scalars().all()

    type_counts = Counter(
        s.session_type.value
        if hasattr(s.session_type, "value")
        else str(s.session_type)
        for s in sessions
    )

    return SessionRangeStats(
        total_sessions=len(sessions),
        by_type=dict(type_counts) if type_counts else None,
    )


@router.get("/detailed-stats", response_model=SessionDetailedStats)
async def get_session_detailed_stats(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get detailed session stats for quarterly reports.

    Returns pool hours, location rankings, busiest sessions, etc.
    Accepts ISO 8601 date strings (with or without timezone).
    """
    from collections import Counter
    from datetime import datetime as _dt

    # Parse date strings flexibly
    parsed_from = _dt.fromisoformat(date_from.replace("Z", "+00:00"))
    parsed_to = _dt.fromisoformat(date_to.replace("Z", "+00:00"))

    result = await db.execute(
        select(Session).where(
            Session.starts_at >= parsed_from,
            Session.starts_at <= parsed_to,
            Session.status.in_([SessionStatus.SCHEDULED, SessionStatus.COMPLETED]),
        )
    )
    sessions = result.scalars().all()

    guest_minutes = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(GuestPass.actual_swim_minutes), 0))
                .join(Session, Session.id == GuestPass.session_id)
                .where(
                    Session.starts_at >= parsed_from,
                    Session.starts_at <= parsed_to,
                    GuestPass.status == "attended",
                )
            )
        ).scalar_one()
        or 0
    )

    if not sessions:
        return SessionDetailedStats(guest_swimmer_hours=round(guest_minutes / 60, 1))

    # Total pool hours (sum of session durations)
    total_hours = sum(
        (s.ends_at - s.starts_at).total_seconds() / 3600 for s in sessions
    )

    # Type breakdown
    type_counts = Counter(
        s.session_type.value
        if hasattr(s.session_type, "value")
        else str(s.session_type)
        for s in sessions
    )

    # Location ranking
    locations = [s.location_name for s in sessions if s.location_name]
    location_counts = Counter(locations)
    most_active = location_counts.most_common(1)[0][0] if location_counts else None

    # Day of week popularity
    DAYS = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    day_counts = Counter(DAYS[s.starts_at.weekday()] for s in sessions)
    most_popular_day = day_counts.most_common(1)[0][0] if day_counts else None

    # Time slot popularity
    def time_slot(hour: int) -> str:
        if hour < 12:
            return "Morning (before noon)"
        elif hour < 17:
            return "Afternoon (noon-5pm)"
        return "Evening (after 5pm)"

    slot_counts = Counter(time_slot(s.starts_at.hour) for s in sessions)
    most_popular_slot = slot_counts.most_common(1)[0][0] if slot_counts else None

    # Session details for per-session info
    details = [
        {
            "id": str(s.id),
            "title": s.title,
            "hours": round((s.ends_at - s.starts_at).total_seconds() / 3600, 2),
            "location": s.location_name,
            "type": s.session_type.value
            if hasattr(s.session_type, "value")
            else str(s.session_type),
            "capacity": s.capacity,
        }
        for s in sessions
    ]

    return SessionDetailedStats(
        total_sessions=len(sessions),
        total_pool_hours=round(total_hours, 1),
        guest_swimmer_hours=round(guest_minutes / 60, 1),
        by_type=dict(type_counts) if type_counts else None,
        most_active_location=most_active,
        most_popular_day=most_popular_day,
        most_popular_time_slot=most_popular_slot,
        session_details=details,
    )


class ConvertedGuestHours(BaseModel):
    member_id: uuid.UUID
    swimmer_hours: float = 0.0


@router.get(
    "/member/{member_id}/converted-guest-hours",
    response_model=ConvertedGuestHours,
)
async def get_converted_guest_hours(
    member_id: uuid.UUID,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> ConvertedGuestHours:
    """Return standalone guest swim history linked to a converted member."""
    minutes = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(GuestPass.actual_swim_minutes), 0))
                .join(Session, Session.id == GuestPass.session_id)
                .where(
                    GuestPass.converted_member_id == member_id,
                    GuestPass.status == "attended",
                    Session.starts_at >= date_from,
                    Session.starts_at <= date_to,
                )
            )
        ).scalar_one()
        or 0
    )
    return ConvertedGuestHours(
        member_id=member_id,
        swimmer_hours=round(minutes / 60, 1),
    )


@router.get(
    "/member/{member_auth_id}/session-commitments",
    response_model=List[MemberSessionCommitment],
)
async def list_member_session_commitments(
    member_auth_id: str,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Confirmed, dated session commitments for member reporting.

    This uses the session's scheduled time as the reporting window, not the
    booking creation time. It lets reporting distinguish "expected to attend"
    from attendance records that happen to exist.
    """
    result = await db.execute(
        select(SessionBooking, Session)
        .join(Session, Session.id == SessionBooking.session_id)
        .where(
            SessionBooking.member_auth_id == member_auth_id,
            SessionBooking.status == SessionBookingStatus.CONFIRMED,
            Session.starts_at >= date_from,
            Session.starts_at <= date_to,
            Session.status.in_(
                [
                    SessionStatus.SCHEDULED,
                    SessionStatus.IN_PROGRESS,
                    SessionStatus.COMPLETED,
                ]
            ),
        )
        .order_by(Session.starts_at.asc())
    )

    return [
        MemberSessionCommitment(
            booking_id=str(booking.id),
            session_id=str(session.id),
            member_id=str(booking.member_id),
            member_auth_id=booking.member_auth_id,
            title=session.title,
            session_type=session.session_type.value,
            session_status=session.status.value,
            starts_at=session.starts_at.isoformat(),
            ends_at=session.ends_at.isoformat(),
            location_name=session.location_name,
            cohort_id=str(session.cohort_id) if session.cohort_id else None,
            pod_id=str(session.pod_id) if session.pod_id else None,
            event_id=str(session.event_id) if session.event_id else None,
            week_number=session.week_number,
        )
        for booking, session in result.all()
    ]


@router.post("/summaries/batch", response_model=List[SessionListSummary])
async def get_session_summaries_batch(
    payload: SessionSummaryBatchRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> List[SessionListSummary]:
    """Return display summaries for many session IDs in one database query."""
    session_ids = list(dict.fromkeys(payload.session_ids))
    if not session_ids:
        return []

    sessions = (
        (await db.execute(select(Session).where(Session.id.in_(session_ids))))
        .scalars()
        .all()
    )
    by_id = {session.id: session for session in sessions}
    return [
        SessionListSummary(
            id=str(session.id),
            title=session.title,
            session_type=session.session_type.value,
            starts_at=session.starts_at.isoformat(),
            location_name=session.location_name,
            location=session.location.value if session.location else None,
        )
        for session_id in session_ids
        if (session := by_id.get(session_id)) is not None
    ]


@router.get("/durations")
async def get_session_durations(
    ids: str = Query(..., description="Comma-separated session UUIDs"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Return duration in hours for a list of session IDs.

    Used by attendance service to compute per-member pool hours.
    """
    import uuid as _uuid

    session_ids = []
    for sid in ids.split(","):
        sid = sid.strip()
        if sid:
            try:
                session_ids.append(_uuid.UUID(sid))
            except ValueError:
                continue

    if not session_ids:
        return []

    result = await db.execute(select(Session).where(Session.id.in_(session_ids)))
    sessions = result.scalars().all()

    return [
        {
            "session_id": str(s.id),
            "duration_hours": round(
                (s.ends_at - s.starts_at).total_seconds() / 3600, 2
            ),
        }
        for s in sessions
    ]


# NOTE: Parameterized routes must come AFTER all static routes to avoid
# "durations", "detailed-stats", etc. being matched as {session_id}.


@router.get("/{session_id}", response_model=SessionBasic)
async def get_session_by_id(
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Look up a session by ID."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    booking_rows = (
        await db.execute(
            select(
                SessionBooking.member_id,
                SessionBooking.party_size,
            ).where(
                SessionBooking.session_id == session.id,
                SessionBooking.status == SessionBookingStatus.CONFIRMED,
            )
        )
    ).all()
    confirmed_member_ids = [str(member_id) for member_id, _ in booking_rows]
    occupied_slots = sum(int(party_size or 1) for _, party_size in booking_rows)
    coach_ids = (
        (
            await db.execute(
                select(SessionCoach.coach_id).where(
                    SessionCoach.session_id == session.id
                )
            )
        )
        .scalars()
        .all()
    )
    coach_member_ids = [str(coach_id) for coach_id in coach_ids]

    return SessionBasic(
        id=str(session.id),
        title=session.title,
        description=session.description,
        notes=session.notes,
        session_type=session.session_type.value,
        status=session.status.value,
        starts_at=session.starts_at.isoformat(),
        ends_at=session.ends_at.isoformat(),
        pool_id=str(session.pool_id) if session.pool_id else None,
        location_name=session.location_name,
        location_address=session.location_address,
        location=session.location.value if session.location else None,
        cohort_id=str(session.cohort_id) if session.cohort_id else None,
        pod_id=str(session.pod_id) if session.pod_id else None,
        capacity=session.capacity,
        pool_fee=session.pool_fee,
        ride_share_fee=session.ride_share_fee,
        occupied_slots=occupied_slots,
        confirmed_booking_member_ids=confirmed_member_ids,
        coach_member_ids=coach_member_ids,
        week_number=session.week_number,
        lesson_title=session.lesson_title,
        timezone=session.timezone,
    )


@router.get("/{session_id}/access", response_model=MemberSessionAccessResponse)
async def get_member_session_access(
    session_id: uuid.UUID,
    member_auth_id: str = Query(..., min_length=1, max_length=128),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> MemberSessionAccessResponse:
    """Return the backend-owned access decision used by payment services."""
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        member = await get_member_by_auth_id(
            member_auth_id,
            calling_service="sessions",
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not verify the member profile. Please try again.",
        ) from exc
    if not member or not member.get("id"):
        raise HTTPException(status_code=404, detail="Member profile not found")
    try:
        member_id = uuid.UUID(str(member["id"]))
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Members service returned an invalid member profile",
        ) from exc

    booking = (
        await db.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == session_id,
                SessionBooking.member_id == member_id,
                SessionBooking.status == SessionBookingStatus.CONFIRMED,
            )
        )
    ).scalar_one_or_none()

    if booking is None:
        member_payload = await get_member_session_access_payload(
            member_id=member_id,
            calling_service="sessions",
        )
    else:
        member_payload = {"id": str(member_id), "member_id": str(member_id)}

    access = await evaluate_session_access_for_member(
        session=session,
        member_payload=member_payload,
        now=utc_now(),
        calling_service="sessions",
        confirmed_booking=booking is not None,
    )
    return MemberSessionAccessResponse(
        member_id=member_id,
        confirmed_booking=booking is not None,
        confirmed_booking_id=booking.id if booking else None,
        required_tier=access.required_tier,
        visible=access.visible,
        bookable=access.bookable,
        digest_eligible=access.digest_eligible,
        prompt_eligible=access.prompt_eligible,
        sign_in_allowed=access.sign_in_allowed,
        sign_in_eligible=access.sign_in_eligible,
        reason=access.reason,
        message=denial_message(access.reason) if access.reason else None,
    )


@router.get("/cohorts/{cohort_id}/next-session", response_model=NextSessionResponse)
async def get_next_session_for_cohort(
    cohort_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the next upcoming session for a cohort."""
    now = utc_now()
    result = await db.execute(
        select(Session)
        .where(
            Session.cohort_id == cohort_id,
            Session.starts_at > now,
            Session.status == SessionStatus.SCHEDULED,
        )
        .order_by(Session.starts_at.asc())
        .limit(1)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="No upcoming session found")
    return NextSessionResponse(
        starts_at=session.starts_at.isoformat(),
        title=session.title,
        location_name=session.location_name,
    )


@router.get("/cohorts/{cohort_id}/session-ids", response_model=List[str])
async def get_session_ids_for_cohort(
    cohort_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get all session IDs for a cohort."""
    result = await db.execute(
        select(Session.id)
        .where(Session.cohort_id == cohort_id)
        .order_by(Session.starts_at.asc())
    )
    return [str(row[0]) for row in result.all()]


@router.get("/{session_id}/confirmed-booking-member-ids", response_model=List[str])
async def get_confirmed_booking_member_ids(
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Member IDs with a CONFIRMED booking for this session (the 'expected to
    attend' set). attendance-service uses this to pre-fill the coach attendance
    sheet — default Present if booked, Absent if not."""
    rows = (
        (
            await db.execute(
                select(SessionBooking.member_id).where(
                    SessionBooking.session_id == session_id,
                    SessionBooking.status == SessionBookingStatus.CONFIRMED,
                )
            )
        )
        .scalars()
        .all()
    )
    return [str(m) for m in rows]


@router.get("/cohorts/{cohort_id}/completed-session-ids", response_model=List[str])
async def get_completed_session_ids_for_cohort(
    cohort_id: uuid.UUID,
    # datetime, not str: binds as timestamptz (str 500s the starts_at comparison)
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get completed session IDs for a cohort, optionally filtered by date range."""
    query = select(Session.id).where(
        Session.cohort_id == cohort_id,
        Session.status == SessionStatus.COMPLETED,
    )
    if start_date:
        query = query.where(Session.starts_at >= start_date)
    if end_date:
        query = query.where(Session.starts_at <= end_date)
    query = query.order_by(Session.starts_at.asc())
    result = await db.execute(query)
    return [str(row[0]) for row in result.all()]


@router.get("/cohorts/{cohort_id}/sessions", response_model=List[SessionBasic])
async def get_sessions_for_cohort_internal(
    cohort_id: uuid.UUID,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    statuses: str = Query(
        "scheduled,in_progress,completed",
        description="Comma-separated session statuses to include.",
    ),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get dated cohort sessions for reporting and academy integrations."""
    parsed_statuses: list[SessionStatus] = []
    invalid: list[str] = []
    for raw_status in [s.strip() for s in statuses.split(",") if s.strip()]:
        try:
            parsed_statuses.append(SessionStatus(raw_status))
        except ValueError:
            invalid.append(raw_status)
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status value(s): {', '.join(invalid)}",
        )

    query = select(Session).where(Session.cohort_id == cohort_id)
    if parsed_statuses:
        query = query.where(Session.status.in_(parsed_statuses))
    if start_date:
        query = query.where(Session.starts_at >= start_date)
    if end_date:
        query = query.where(Session.starts_at <= end_date)
    query = query.order_by(Session.starts_at.asc())

    result = await db.execute(query)
    sessions = result.scalars().all()
    return [
        SessionBasic(
            id=str(s.id),
            title=s.title,
            session_type=s.session_type.value,
            status=s.status.value,
            starts_at=s.starts_at.isoformat(),
            ends_at=s.ends_at.isoformat(),
            pool_id=str(s.pool_id) if s.pool_id else None,
            location_name=s.location_name,
            location_address=s.location_address,
            location=s.location.value if s.location else None,
            cohort_id=str(s.cohort_id) if s.cohort_id else None,
            pod_id=str(s.pod_id) if s.pod_id else None,
            capacity=s.capacity,
            pool_fee=s.pool_fee,
            week_number=s.week_number,
            lesson_title=s.lesson_title,
            timezone=s.timezone,
        )
        for s in sessions
    ]


@router.post(
    "/cohorts/{cohort_id}/generate",
    response_model=GenerateCohortSessionsResponse,
)
async def generate_cohort_sessions(
    cohort_id: uuid.UUID,
    body: GenerateCohortSessionsRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Generate the weekly cohort_class sessions for a date window.

    Called by academy-service when a cohort extension is approved so the added
    weeks get sessions automatically (mirroring the create-cohort wizard).
    Idempotent: dates that already have a session are skipped.
    """
    from services.sessions_service.services.cohort_sessions import (
        generate_sessions_for_cohort,
    )

    result = await generate_sessions_for_cohort(
        db, cohort_id, body.from_date, body.to_date
    )
    await db.commit()

    from services.sessions_service.services.notifications import (
        trigger_session_published_notifications,
    )

    for entry in result.get("created_sessions", []):
        await trigger_session_published_notifications(
            session_id=entry["session_id"],
            starts_at=datetime.fromisoformat(entry["starts_at"]),
        )

    return GenerateCohortSessionsResponse(**result)


@router.get("/{session_id}/coaches", response_model=List[str])
async def get_session_coach_ids(
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get coach member IDs for a session."""
    result = await db.execute(
        select(SessionCoach.coach_id).where(SessionCoach.session_id == session_id)
    )
    return [str(row[0]) for row in result.all()]


# ---------------------------------------------------------------------------
# A1 Phase 3.3: SessionBooking internal endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/bookings/bundle/reserve",
    response_model=BundleBookingReserveResponse,
)
async def reserve_bundle_bookings(
    payload: BundleBookingReserveRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> BundleBookingReserveResponse:
    """Reserve capacity and snapshot server-owned prices for a bundle payment."""
    member = await get_member_by_auth_id(
        payload.member_auth_id, calling_service="sessions"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(str(member["id"]))

    sessions = (
        (
            await db.execute(
                select(Session)
                .where(Session.id.in_(payload.session_ids))
                .order_by(Session.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    session_map = {session.id: session for session in sessions}
    missing = [
        session_id
        for session_id in payload.session_ids
        if session_id not in session_map
    ]
    if missing:
        raise HTTPException(
            status_code=404, detail="One or more sessions were not found"
        )

    existing_rows = (
        (
            await db.execute(
                select(SessionBooking).where(
                    SessionBooking.member_id == member_id,
                    SessionBooking.session_id.in_(payload.session_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_session = {booking.session_id: booking for booking in existing_rows}
    now = utc_now()

    for session_id in payload.session_ids:
        existing = existing_by_session.get(session_id)
        if existing is None:
            continue
        if existing.status == SessionBookingStatus.CONFIRMED:
            raise HTTPException(
                status_code=409,
                detail=f"Session {session_id} is already booked",
            )
        if (
            existing.status == SessionBookingStatus.PENDING
            and (existing.expires_at is None or existing.expires_at > now)
            and existing.payment_intent_id != payload.payment_intent_id
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A payment is already in progress for one or more selected "
                    "sessions. Complete it or wait for the reservation to expire."
                ),
            )

    member_payload = await get_member_session_access_payload(
        member_id=member_id,
        calling_service="sessions",
    )
    for session_id in payload.session_ids:
        session = session_map[session_id]
        access = await evaluate_session_access_for_member(
            session=session,
            member_payload=member_payload,
            now=now,
            calling_service="sessions",
        )
        if not access.bookable:
            raise HTTPException(
                status_code=403,
                detail=f"{session.title}: {denial_message(access.reason)}",
            )

    lines: list[BundleBookingLineResponse] = []
    for session_id in payload.session_ids:
        session = session_map[session_id]
        await assert_booking_capacity(
            db,
            session=session,
            member_id=member_id,
            new_party_size=1,
        )
        fee_kobo = int(session.pool_fee or 0)
        booking = existing_by_session.get(session_id)
        if booking is None:
            booking = SessionBooking(
                session_id=session_id,
                member_id=member_id,
                member_auth_id=payload.member_auth_id,
                status=SessionBookingStatus.PENDING,
                channel=BookingChannel.BUNDLE_CART,
                party_size=1,
                fee_amount_kobo=fee_kobo,
                payment_intent_id=payload.payment_intent_id,
                booked_at=now,
                expires_at=now + timedelta(minutes=PENDING_TTL_MINUTES),
            )
            db.add(booking)
        else:
            booking.member_auth_id = payload.member_auth_id
            booking.status = SessionBookingStatus.PENDING
            booking.channel = BookingChannel.BUNDLE_CART
            booking.party_size = 1
            booking.fee_amount_kobo = fee_kobo
            booking.payment_intent_id = payload.payment_intent_id
            booking.wallet_transaction_id = None
            booking.confirmed_at = None
            booking.cancelled_at = None
            booking.booked_at = now
            booking.expires_at = now + timedelta(minutes=PENDING_TTL_MINUTES)
        await db.flush()
        lines.append(
            BundleBookingLineResponse(
                session_id=session_id,
                booking_id=booking.id,
                amount_kobo=fee_kobo,
            )
        )

    await db.commit()
    return BundleBookingReserveResponse(
        member_id=member_id,
        payment_intent_id=payload.payment_intent_id,
        pool_total_kobo=sum(line.amount_kobo for line in lines),
        lines=lines,
    )


@router.post(
    "/bookings/bundle/confirm",
    response_model=BundleBookingConfirmResponse,
)
async def confirm_bundle_bookings(
    payload: BundleBookingConfirmRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> BundleBookingConfirmResponse:
    """Confirm a paid bundle atomically and idempotently.

    Expired holds may still be confirmed after a delayed provider callback, but
    only while every session remains upcoming and has capacity. Either every
    booking commits or none of them does.
    """
    booking_refs = (
        await db.execute(
            select(SessionBooking.id, SessionBooking.session_id)
            .where(SessionBooking.id.in_(payload.booking_ids))
            .order_by(SessionBooking.id)
        )
    ).all()
    if len({row.id for row in booking_refs}) != len(payload.booking_ids):
        raise HTTPException(
            status_code=404, detail="One or more bookings were not found"
        )

    # Reserve takes session locks before mutating booking rows. Confirmation
    # uses the same global order so checkout and callback traffic cannot form
    # a sessions->bookings / bookings->sessions deadlock cycle.
    session_ids = sorted({row.session_id for row in booking_refs})
    sessions = (
        (
            await db.execute(
                select(Session)
                .where(Session.id.in_(session_ids))
                .order_by(Session.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    session_map = {session.id: session for session in sessions}
    if len(session_map) != len(session_ids):
        raise HTTPException(
            status_code=404, detail="One or more sessions were not found"
        )

    rows = (
        (
            await db.execute(
                select(SessionBooking)
                .where(SessionBooking.id.in_(payload.booking_ids))
                .order_by(SessionBooking.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    booking_map = {booking.id: booking for booking in rows}
    if len(booking_map) != len(payload.booking_ids):
        raise HTTPException(
            status_code=404, detail="One or more bookings were not found"
        )

    ordered_bookings = [booking_map[booking_id] for booking_id in payload.booking_ids]
    for booking in ordered_bookings:
        if booking.member_auth_id != payload.member_auth_id:
            raise HTTPException(
                status_code=409,
                detail="A booking belongs to a different member",
            )
        if booking.payment_intent_id != payload.payment_intent_id:
            raise HTTPException(
                status_code=409,
                detail="A booking belongs to a different payment intent",
            )
        if booking.status not in {
            SessionBookingStatus.PENDING,
            SessionBookingStatus.EXPIRED,
            SessionBookingStatus.CONFIRMED,
        }:
            raise HTTPException(
                status_code=422,
                detail=f"Cannot confirm a booking with status={booking.status.value}",
            )

    now = utc_now()
    for booking in ordered_bookings:
        if booking.status == SessionBookingStatus.CONFIRMED:
            continue
        session = session_map[booking.session_id]
        if session.status != SessionStatus.SCHEDULED or session.starts_at <= now:
            raise HTTPException(
                status_code=409,
                detail="A paid reservation can no longer be confirmed",
            )

    needs_capacity_check = [
        booking
        for booking in ordered_bookings
        if booking.status != SessionBookingStatus.CONFIRMED
        and (
            booking.status == SessionBookingStatus.EXPIRED
            or (booking.expires_at is not None and booking.expires_at <= now)
        )
    ]
    if needs_capacity_check:
        for booking in needs_capacity_check:
            session = session_map[booking.session_id]
            if session.status != SessionStatus.SCHEDULED or session.starts_at <= now:
                raise HTTPException(
                    status_code=409,
                    detail="An expired reservation can no longer be restored",
                )
            await assert_booking_capacity(
                db,
                session=session,
                member_id=booking.member_id,
                new_party_size=booking.party_size,
            )

    for booking in ordered_bookings:
        if booking.status != SessionBookingStatus.CONFIRMED:
            booking.status = SessionBookingStatus.CONFIRMED
            booking.confirmed_at = now
            booking.expires_at = None
        if (
            payload.wallet_transaction_id is not None
            and booking.wallet_transaction_id is None
        ):
            booking.wallet_transaction_id = payload.wallet_transaction_id

    await db.commit()
    for booking in ordered_bookings:
        await db.refresh(booking)
        await sync_booking_attendance(booking)
    return BundleBookingConfirmResponse(
        confirmed=len(ordered_bookings),
        bookings=ordered_bookings,
    )


@router.post(
    "/bookings/bundle/release",
    response_model=BundleBookingReleaseResponse,
)
async def release_bundle_bookings(
    payload: BundleBookingReleaseRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> BundleBookingReleaseResponse:
    """Release only pending reservations owned by an abandoned bundle intent."""
    rows = (
        (
            await db.execute(
                select(SessionBooking)
                .where(
                    SessionBooking.member_auth_id == payload.member_auth_id,
                    SessionBooking.payment_intent_id == payload.payment_intent_id,
                    SessionBooking.status == SessionBookingStatus.PENDING,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    now = utc_now()
    for booking in rows:
        booking.status = SessionBookingStatus.EXPIRED
        booking.expires_at = now
    if rows:
        await db.commit()
    return BundleBookingReleaseResponse(released=len(rows))


@router.get(
    "/{session_id}/bookings/by-member/{member_id}",
    response_model=SessionBookingResponse,
)
async def get_booking_for_session_member(
    session_id: uuid.UUID,
    member_id: uuid.UUID,
    status: Optional[str] = Query(
        None, description="Filter by booking status (e.g. 'confirmed')"
    ),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Service-role lookup: SessionBooking for (session, member).

    Used by attendance_service's sign-in flow to link the AttendanceRecord
    being created back to its originating booking. 404 if no booking
    matches the filter — caller treats that as "walk-in" and continues.
    """
    query = select(SessionBooking).where(
        SessionBooking.session_id == session_id,
        SessionBooking.member_id == member_id,
    )
    if status:
        try:
            query = query.where(SessionBooking.status == SessionBookingStatus(status))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid status={status}")
    booking = (await db.execute(query)).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="No booking found")
    return booking


@router.get(
    "/bookings/confirmed",
    response_model=List[SessionBookingResponse],
)
async def list_confirmed_bookings_since(
    since: datetime = Query(..., description="Lower bound on booked_at (ISO 8601)"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Service-role: list CONFIRMED bookings since `since`.

    Used by attendance_service's nightly NO_SHOW sweep to find recent
    confirmed bookings that may need an ABSENT AttendanceRecord created.
    """
    query = (
        select(SessionBooking)
        .where(
            SessionBooking.status == SessionBookingStatus.CONFIRMED,
            SessionBooking.booked_at >= since,
        )
        .order_by(SessionBooking.booked_at.asc())
    )
    return (await db.execute(query)).scalars().all()


@router.get(
    "/bookings/{booking_id}",
    response_model=SessionBookingResponse,
)
async def get_booking_internal(
    booking_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Service-role: fetch a single SessionBooking by id.

    Used by payments_service to generate an admin-issued pay link for a
    booking (purpose=session_booking). Returns 404 if not found.
    """
    booking = (
        await db.execute(select(SessionBooking).where(SessionBooking.id == booking_id))
    ).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking


@router.post(
    "/bookings/{booking_id}/confirm",
    response_model=SessionBookingResponse,
)
async def internal_confirm_booking(
    booking_id: uuid.UUID,
    confirm_in: BookingConfirmRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Service-role variant of /sessions/bookings/{id}/confirm.

    Future: payments_service webhook calls this when a SESSION_BOOKING
    payment intent clears (so the booking gets confirmed even if the
    member closed the browser mid-checkout).
    """
    booking_ref = (
        await db.execute(
            select(SessionBooking.id, SessionBooking.session_id).where(
                SessionBooking.id == booking_id
            )
        )
    ).one_or_none()
    if booking_ref is None:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Match the bundle lock order: session first, then booking. This lets an
    # expired payment callback safely restore capacity without deadlocking a
    # simultaneous reservation.
    session = (
        await db.execute(
            select(Session)
            .where(Session.id == booking_ref.session_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    booking = (
        await db.execute(
            select(SessionBooking)
            .where(SessionBooking.id == booking_id)
            .with_for_update()
        )
    ).scalar_one()
    if (
        confirm_in.member_auth_id is not None
        and booking.member_auth_id != confirm_in.member_auth_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Booking belongs to a different member",
        )
    if (
        booking.payment_intent_id is not None
        and confirm_in.payment_intent_id is not None
        and booking.payment_intent_id != confirm_in.payment_intent_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Booking belongs to a different payment intent",
        )
    if booking.status == SessionBookingStatus.CONFIRMED:
        # Walk-in flow: admin recorded the booking as CONFIRMED at the pool,
        # member later paid via a generated Paystack link. Backfill the
        # payment linkage so reports can join booking → payment without
        # going through the metadata JSON. Only fill blanks — never
        # overwrite an existing link.
        updated = False
        if (
            confirm_in.payment_intent_id is not None
            and booking.payment_intent_id is None
        ):
            booking.payment_intent_id = confirm_in.payment_intent_id
            updated = True
        if (
            confirm_in.wallet_transaction_id is not None
            and booking.wallet_transaction_id is None
        ):
            booking.wallet_transaction_id = confirm_in.wallet_transaction_id
            updated = True
        if updated:
            await db.commit()
            await db.refresh(booking)
        await sync_booking_attendance(booking)
        return booking
    now = utc_now()
    if session.status != SessionStatus.SCHEDULED or session.starts_at <= now:
        raise HTTPException(
            status_code=409,
            detail="This paid reservation can no longer be confirmed",
        )
    if booking.status == SessionBookingStatus.EXPIRED:
        await assert_booking_capacity(
            db,
            session=session,
            member_id=booking.member_id,
            new_party_size=booking.party_size,
        )
    elif booking.status != SessionBookingStatus.PENDING:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot confirm a booking with status={booking.status.value}.",
        )
    booking.status = SessionBookingStatus.CONFIRMED
    booking.confirmed_at = utc_now()
    booking.expires_at = None
    if confirm_in.payment_intent_id is not None:
        booking.payment_intent_id = confirm_in.payment_intent_id
    if confirm_in.wallet_transaction_id is not None:
        booking.wallet_transaction_id = confirm_in.wallet_transaction_id
    await db.commit()
    await db.refresh(booking)
    await sync_booking_attendance(booking)
    return booking


@router.post("/bookings/bulk", response_model=BulkBookingResponse)
async def bulk_create_bookings(
    payload: BulkBookingRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Service-role bulk-create for corporate-wellness onboarding.

    Each row is created at status=CONFIRMED (sponsor-paid up front),
    channel=CORPORATE_BULK, with corporate_program_id set. Idempotent:
    pre-existing (session, member) pairs are reported as `skipped` and
    the existing row is returned unchanged.
    """
    created_rows: list[SessionBooking] = []
    skipped = 0
    now = utc_now()

    for item in payload.items:
        existing = (
            await db.execute(
                select(SessionBooking).where(
                    SessionBooking.session_id == item.session_id,
                    SessionBooking.member_id == item.member_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            created_rows.append(existing)
            continue

        booking = SessionBooking(
            session_id=item.session_id,
            member_id=item.member_id,
            member_auth_id=item.member_auth_id,
            status=SessionBookingStatus.CONFIRMED,
            channel=BookingChannel.CORPORATE_BULK,
            fee_amount_kobo=item.fee_amount_kobo,
            corporate_program_id=payload.corporate_program_id,
            booked_at=now,
            confirmed_at=now,
        )
        db.add(booking)
        created_rows.append(booking)

    await db.commit()
    for booking in created_rows:
        await db.refresh(booking)
        await sync_booking_attendance(booking)

    return BulkBookingResponse(
        created=len(payload.items) - skipped,
        skipped=skipped,
        bookings=[
            SessionBookingResponse.model_validate(b, from_attributes=True)
            for b in created_rows
        ],
    )
