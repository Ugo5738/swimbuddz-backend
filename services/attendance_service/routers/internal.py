"""Internal service-to-service endpoints for attendance-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from collections import Counter
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.attendance_service.models import AttendanceRecord
from services.attendance_service.models.enums import AttendanceRole, AttendanceStatus

router = APIRouter(prefix="/internal/attendance", tags=["internal"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class AttendanceRecordBasic(BaseModel):
    id: str
    session_id: str
    member_id: str
    status: str
    role: str = "swimmer"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/member/{member_id}",
    response_model=List[AttendanceRecordBasic],
)
async def get_member_attendance(
    member_id: uuid.UUID,
    session_ids: Optional[str] = Query(
        None, description="Comma-separated session IDs to filter by"
    ),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Get attendance records for a member, optionally filtered by session IDs."""
    query = select(AttendanceRecord).where(
        AttendanceRecord.member_id == member_id,
    )
    if session_ids:
        ids = [uuid.UUID(sid.strip()) for sid in session_ids.split(",") if sid.strip()]
        query = query.where(AttendanceRecord.session_id.in_(ids))

    result = await db.execute(query)
    records = result.scalars().all()

    return [
        AttendanceRecordBasic(
            id=str(r.id),
            session_id=str(r.session_id),
            member_id=str(r.member_id),
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            role=r.role.value if hasattr(r.role, "value") else str(r.role),
        )
        for r in records
    ]


@router.get(
    "/session/{session_id}/member-ids",
    response_model=List[str],
)
async def get_session_attendee_member_ids(
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Return distinct member ID strings for everyone who has an attendance
    record for the given session.  Used by communications-service to find
    who should receive session-related notifications."""
    query = (
        select(AttendanceRecord.member_id)
        .where(
            AttendanceRecord.session_id == session_id,
            AttendanceRecord.member_id.is_not(None),
        )
        .distinct()
    )
    result = await db.execute(query)
    return [str(mid) for mid in result.scalars().all()]


class SessionAttendanceCount(BaseModel):
    session_id: uuid.UUID
    attended: int = 0
    member_attended: int = 0
    booking_guest_attended: int = 0
    participant_ids: list[uuid.UUID] = Field(default_factory=list)
    member_ids: list[uuid.UUID] = Field(default_factory=list)


@router.get(
    "/session-counts",
    response_model=list[SessionAttendanceCount],
)
async def get_session_attendance_counts(
    ids: str = Query(..., description="Comma-separated session IDs"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Return actual PRESENT/LATE attendance by session and subject kind.

    Sessions/reporting own the scheduled dates, so callers first select the
    sessions in their reporting window and then ask Attendance for counts. This
    avoids attributing a retrospectively-entered walk-in to the day the admin
    happened to enter it.
    """
    session_ids = [
        uuid.UUID(value.strip()) for value in ids.split(",") if value.strip()
    ]
    if not session_ids:
        return []
    if len(session_ids) > 500:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422, detail="At most 500 session IDs are supported"
        )

    rows = (
        (
            await db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.session_id.in_(session_ids),
                    AttendanceRecord.role.in_(
                        [AttendanceRole.SWIMMER, AttendanceRole.GUEST]
                    ),
                    AttendanceRecord.status.in_(
                        [AttendanceStatus.PRESENT, AttendanceStatus.LATE]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    counts: dict[uuid.UUID, dict] = {
        session_id: {
            "attended": 0,
            "member_attended": 0,
            "booking_guest_attended": 0,
            "participant_ids": [],
            "member_ids": [],
        }
        for session_id in session_ids
    }
    for row in rows:
        bucket = counts[row.session_id]
        bucket["attended"] += 1
        if row.member_id is not None:
            bucket["member_attended"] += 1
            bucket["member_ids"].append(row.member_id)
        elif row.booking_guest_id is not None:
            bucket["booking_guest_attended"] += 1
        elif row.participant_id is not None:
            bucket["participant_ids"].append(row.participant_id)

    return [
        SessionAttendanceCount(session_id=session_id, **counts[session_id])
        for session_id in session_ids
    ]


# ---------------------------------------------------------------------------
# Reporting aggregation
# ---------------------------------------------------------------------------


class MemberAttendanceStats(BaseModel):
    """Aggregated attendance stats for a member over a date range."""

    total_present: int = 0
    total_late: int = 0
    total_absent: int = 0
    total_excused: int = 0
    total_sessions: int = 0
    by_type: dict | None = None
    by_day: dict | None = None
    by_location: dict | None = None
    favorite_day: str | None = None
    favorite_location: str | None = None
    weekly_attendance: list[bool] | None = None
    events_attended: int = 0
    total_pool_hours: float = 0.0
    attendance_available: bool = False
    attended_sessions: list[dict] = Field(default_factory=list)


@router.get(
    "/stats/member/{member_auth_id}",
    response_model=MemberAttendanceStats,
)
async def get_member_attendance_stats(
    member_auth_id: str,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate attendance stats for a member within a date range.

    Used by the reporting service for quarterly reports.
    The member_auth_id is matched against members.auth_id via the member_id FK.
    """
    from services.attendance_service.models.core import MemberRef

    # Look up member_id from auth_id
    member_result = await db.execute(
        select(MemberRef.id).where(MemberRef.auth_id == member_auth_id)
    )
    member_uuid = member_result.scalar_one_or_none()
    if member_uuid is None:
        return MemberAttendanceStats()

    # Session dates own the reporting window. Retrospective attendance entry
    # must not move July swims into October or erase one-hour Academy lessons.
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from libs.common.config import get_settings
    from libs.common.datetime_utils import utc_now
    from libs.common.reporting import enum_value, reporting_window
    from libs.common.service_client import internal_post

    start, end = reporting_window(date_from, date_to)
    records = (
        (
            await db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.member_id == member_uuid,
                    AttendanceRecord.role.in_(
                        [AttendanceRole.SWIMMER, AttendanceRole.GUEST]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    if not records:
        return MemberAttendanceStats(attendance_available=True)
    ids = sorted({str(r.session_id) for r in records})
    sessions = {}
    try:
        for offset in range(0, len(ids), 200):
            response = await internal_post(
                service_url=get_settings().SESSIONS_SERVICE_URL,
                path="/internal/sessions/summaries/batch",
                calling_service="attendance",
                json={"session_ids": ids[offset : offset + 200]},
                timeout=20.0,
            )
            response.raise_for_status()
            for session in response.json():
                when = datetime.fromisoformat(
                    session["starts_at"].replace("Z", "+00:00")
                )
                if start <= when < end and session.get("status") in {
                    "scheduled",
                    "in_progress",
                    "completed",
                }:
                    sessions[str(session["id"])] = session
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Session dates unavailable for attendance reporting"
        ) from exc
    # A member's swimmer record is unique per session; defensively deduplicate.
    in_window = {str(r.session_id): r for r in records if str(r.session_id) in sessions}
    status_counts = Counter(enum_value(r.status) for r in in_window.values())
    attended = [
        sessions[sid]
        for sid, r in in_window.items()
        if enum_value(r.status) in {"present", "late"}
    ]
    days, locations, types = Counter(), Counter(), Counter()
    weeks = set()
    pool_hours = 0.0
    lagos = ZoneInfo("Africa/Lagos")
    for session in attended:
        when = datetime.fromisoformat(
            session["starts_at"].replace("Z", "+00:00")
        ).astimezone(lagos)
        iso = when.isocalendar()
        weeks.add((iso.year, iso.week))
        days[when.strftime("%A")] += 1
        if session.get("location_name"):
            locations[session["location_name"]] += 1
        types[session["session_type"]] += 1
        if session.get("ends_at"):
            finished = datetime.fromisoformat(session["ends_at"].replace("Z", "+00:00"))
            pool_hours += max(0, (finished - when).total_seconds() / 3600)
    weekly = []
    current = start.astimezone(lagos)
    while current < min(end, utc_now()):
        iso = current.isocalendar()
        weekly.append((iso.year, iso.week) in weeks)
        current += timedelta(weeks=1)
    return MemberAttendanceStats(
        total_present=status_counts.get("present", 0),
        total_late=status_counts.get("late", 0),
        total_absent=status_counts.get("absent", 0),
        total_excused=status_counts.get("excused", 0),
        total_sessions=len(in_window),
        by_type=dict(types),
        by_day=dict(days),
        by_location=dict(locations),
        favorite_day=days.most_common(1)[0][0] if days else None,
        favorite_location=locations.most_common(1)[0][0] if locations else None,
        weekly_attendance=weekly,
        total_pool_hours=round(pool_hours, 1),
        events_attended=types.get("event", 0),
        attendance_available=True,
        attended_sessions=attended,
    )


class MemberSwimMilestones(BaseModel):
    """All-time attended swimming milestones for a member (Lagos calendar weeks)."""

    total_attended_swims: int = 0
    active_weeks: int = 0
    current_streak_weeks: int = 0
    best_streak_weeks: int = 0
    attendance_available: bool = False


def compute_milestone_metrics(
    session_starts: list[datetime], today_lagos
) -> MemberSwimMilestones:
    """Compute all-time attended swim count, active weeks, and streaks in Lagos time."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    lagos = ZoneInfo("Africa/Lagos")
    local_dates = [dt.astimezone(lagos).date() for dt in session_starts]
    week_mondays = sorted({d - timedelta(days=d.weekday()) for d in local_dates})
    week_set = set(week_mondays)

    best_streak = 0
    run = 0
    prev_week = None
    one_week = timedelta(weeks=1)
    for wk in week_mondays:
        if prev_week is not None and wk == prev_week + one_week:
            run += 1
        else:
            run = 1
        if run > best_streak:
            best_streak = run
        prev_week = wk

    this_monday = today_lagos - timedelta(days=today_lagos.weekday())
    cursor = this_monday if this_monday in week_set else this_monday - one_week
    current_streak = 0
    while cursor in week_set:
        current_streak += 1
        cursor -= one_week

    return MemberSwimMilestones(
        total_attended_swims=len(local_dates),
        active_weeks=len(week_mondays),
        current_streak_weeks=current_streak,
        best_streak_weeks=best_streak,
        attendance_available=True,
    )


@router.get(
    "/member-milestones/{member_auth_id}",
    response_model=MemberSwimMilestones,
)
async def get_member_swim_milestones(
    member_auth_id: str,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Return all-time attended swim counts and weekly streaks for rewards milestones.

    Counts persisted PRESENT/LATE swimmer attendance on non-cancelled sessions that
    have already started (excluding future pre-booked default-Present rows).
    """
    from zoneinfo import ZoneInfo

    from libs.common.config import get_settings
    from libs.common.datetime_utils import utc_now
    from libs.common.service_client import internal_post
    from services.attendance_service.models.core import MemberRef

    member_result = await db.execute(
        select(MemberRef.id).where(MemberRef.auth_id == member_auth_id)
    )
    member_uuid = member_result.scalar_one_or_none()
    if member_uuid is None:
        return MemberSwimMilestones(attendance_available=True)

    session_ids = list(
        (
            await db.scalars(
                select(AttendanceRecord.session_id)
                .where(
                    AttendanceRecord.member_id == member_uuid,
                    AttendanceRecord.role.in_(
                        [AttendanceRole.SWIMMER, AttendanceRole.GUEST]
                    ),
                    AttendanceRecord.status.in_(
                        [AttendanceStatus.PRESENT, AttendanceStatus.LATE]
                    ),
                )
                .distinct()
            )
        ).all()
    )
    if not session_ids:
        return MemberSwimMilestones(attendance_available=True)

    now = utc_now()
    ids = sorted(str(sid) for sid in session_ids)
    attended_starts: list[datetime] = []
    seen_session_ids: set[str] = set()
    try:
        for offset in range(0, len(ids), 200):
            response = await internal_post(
                service_url=get_settings().SESSIONS_SERVICE_URL,
                path="/internal/sessions/summaries/batch",
                calling_service="attendance",
                json={"session_ids": ids[offset : offset + 200]},
                timeout=10.0,
            )
            response.raise_for_status()
            for session in response.json():
                sid = str(session.get("id") or "")
                if not sid or sid in seen_session_ids:
                    continue
                if session.get("status") not in {
                    "scheduled",
                    "in_progress",
                    "completed",
                }:
                    continue
                raw_start = session.get("starts_at")
                if not raw_start:
                    continue
                when = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
                if when <= now:
                    seen_session_ids.add(sid)
                    attended_starts.append(when)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Session dates unavailable for swim milestones"
        ) from exc

    today_lagos = now.astimezone(ZoneInfo("Africa/Lagos")).date()
    return compute_milestone_metrics(attended_starts, today_lagos)
