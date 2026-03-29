"""Internal service-to-service endpoints for sessions-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.sessions_service.models import Session, SessionCoach, SessionStatus

router = APIRouter(prefix="/internal", tags=["internal"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SessionBasic(BaseModel):
    id: str
    title: str
    session_type: str
    status: str
    starts_at: str
    ends_at: str
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    location: Optional[str] = None
    cohort_id: Optional[str] = None
    capacity: int
    # pool_fee is returned in KOBO (integer) for service-to-service use.
    # Consuming services: call kobo_to_bubbles(pool_fee) to get the Bubble charge.
    pool_fee: Optional[int] = None
    week_number: Optional[int] = None
    lesson_title: Optional[str] = None
    timezone: str = "Africa/Lagos"


class NextSessionResponse(BaseModel):
    starts_at: str
    title: str
    location_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# NOTE: Static path "/sessions/scheduled" must be registered before the
# parameterized "/sessions/{session_id}" to avoid route collision (FastAPI
# matches routes in definition order).


@router.get("/sessions/scheduled", response_model=List[SessionBasic])
async def get_scheduled_sessions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get scheduled sessions within a date range."""
    query = select(Session).where(Session.status == SessionStatus.SCHEDULED)
    if start_date:
        query = query.where(Session.starts_at >= start_date)
    if end_date:
        query = query.where(Session.starts_at < end_date)
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
            location_name=s.location_name,
            location_address=s.location_address,
            location=s.location.value if s.location else None,
            cohort_id=str(s.cohort_id) if s.cohort_id else None,
            capacity=s.capacity,
            pool_fee=s.pool_fee,
            week_number=s.week_number,
            lesson_title=s.lesson_title,
            timezone=s.timezone,
        )
        for s in sessions
    ]


# ---------------------------------------------------------------------------
# Reporting aggregation
# NOTE: Static path "/sessions/range-stats" must be registered before the
# parameterized "/sessions/{session_id}" to avoid route collision.
# ---------------------------------------------------------------------------


class SessionRangeStats(BaseModel):
    """Aggregated session stats for a date range."""

    total_sessions: int = 0
    by_type: dict | None = None
    new_members: int = 0  # placeholder — computed elsewhere


@router.get("/sessions/range-stats", response_model=SessionRangeStats)
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


@router.get("/sessions/{session_id}", response_model=SessionBasic)
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
    return SessionBasic(
        id=str(session.id),
        title=session.title,
        session_type=session.session_type.value,
        status=session.status.value,
        starts_at=session.starts_at.isoformat(),
        ends_at=session.ends_at.isoformat(),
        location_name=session.location_name,
        location_address=session.location_address,
        location=session.location.value if session.location else None,
        cohort_id=str(session.cohort_id) if session.cohort_id else None,
        capacity=session.capacity,
        pool_fee=session.pool_fee,
        week_number=session.week_number,
        lesson_title=session.lesson_title,
        timezone=session.timezone,
    )


@router.get("/cohorts/{cohort_id}/next-session", response_model=NextSessionResponse)
async def get_next_session_for_cohort(
    cohort_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the next upcoming session for a cohort."""
    now = datetime.now(timezone.utc)
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


@router.get("/cohorts/{cohort_id}/completed-session-ids", response_model=List[str])
async def get_completed_session_ids_for_cohort(
    cohort_id: uuid.UUID,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
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


@router.get("/sessions/{session_id}/coaches", response_model=List[str])
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


# ── Detailed reporting stats ──


class SessionDetailedStats(BaseModel):
    """Extended session stats for quarterly reports."""

    total_sessions: int = 0
    total_pool_hours: float = 0.0
    by_type: dict | None = None
    most_active_location: str | None = None
    busiest_session_title: str | None = None
    busiest_session_attendance: int = 0
    most_popular_day: str | None = None
    most_popular_time_slot: str | None = None
    session_details: list[dict] | None = None


@router.get("/sessions/detailed-stats", response_model=SessionDetailedStats)
async def get_session_detailed_stats(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get detailed session stats for quarterly reports.

    Returns pool hours, location rankings, busiest sessions, etc.
    """
    from collections import Counter

    result = await db.execute(
        select(Session).where(
            Session.starts_at >= date_from,
            Session.starts_at <= date_to,
            Session.status.in_([SessionStatus.SCHEDULED, SessionStatus.COMPLETED]),
        )
    )
    sessions = result.scalars().all()

    if not sessions:
        return SessionDetailedStats()

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
        by_type=dict(type_counts) if type_counts else None,
        most_active_location=most_active,
        most_popular_day=most_popular_day,
        most_popular_time_slot=most_popular_slot,
        session_details=details,
    )


@router.get("/sessions/durations")
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
