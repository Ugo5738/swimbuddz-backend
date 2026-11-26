"""Listing endpoints: session attendance, cohort summary, my history."""

import time
import uuid
from typing import List

import httpx
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import (
    get_confirmed_booking_member_ids,
    get_members_bulk,
    get_session_ids_for_cohort,
    get_sessions_by_ids,
    internal_get,
)
from libs.db.session import get_async_db
from services.attendance_service.models import (
    AttendanceRecord,
    AttendanceRole,
    AttendanceStatus,
    MemberRef,
)
from services.attendance_service.schemas import (
    AttendanceResponse,
    CohortAttendanceSummary,
    MemberYtdAttendanceSummary,
    StudentAttendanceSummary,
)

from ._shared import get_current_member, require_admin_or_coach_for_session

router = APIRouter()
logger = get_logger(__name__)


@router.get(
    "/sessions/{session_id}/attendance", response_model=List[AttendanceResponse]
)
async def list_session_attendance(
    session_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all attendees for a session.

    Access control:
    - Admins: Can view attendance for any session
    - Coaches: Can view attendance for sessions in their assigned cohorts
    """
    # Check authorization (admin or coach for this session's cohort)
    await require_admin_or_coach_for_session(session_id, current_user, db)

    query = select(AttendanceRecord).where(AttendanceRecord.session_id == session_id)
    result = await db.execute(query)
    records = result.scalars().all()

    # Bulk-lookup member details
    member_ids = list({str(r.member_id) for r in records if r.member_id is not None})
    members_data = await get_members_bulk(member_ids, calling_service="attendance")
    members_map = {m["id"]: m for m in members_data}

    responses = []
    for attendance in records:
        resp = AttendanceResponse.model_validate(attendance)
        member = (
            members_map.get(str(attendance.member_id), {})
            if attendance.member_id
            else {}
        )
        resp.member_name = (
            f"{member.get('first_name', '')} {member.get('last_name', '')}".strip()
            or None
        )
        resp.member_email = member.get("email")
        responses.append(resp)

    return responses


@router.get("/sessions/{session_id}/booked-member-ids", response_model=List[str])
async def list_session_booked_member_ids(
    session_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Member IDs with a confirmed booking for this session, for the coach
    attendance sheet's default (Present if booked, Absent if not). Same
    access control as the attendance list (admin or the cohort's coach)."""
    await require_admin_or_coach_for_session(session_id, current_user, db)
    return await get_confirmed_booking_member_ids(
        str(session_id), calling_service="attendance"
    )


@router.get(
    "/cohorts/{cohort_id}/attendance/summary", response_model=CohortAttendanceSummary
)
async def get_cohort_attendance_summary(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get attendance summary for all students in a cohort.

    Returns aggregated attendance data: total sessions, per-student attendance rates.

    Access control:
    - Admins: Can view any cohort's attendance
    - Coaches: Can view attendance for their assigned cohorts
    """
    from libs.auth.dependencies import require_coach_for_cohort

    # Check authorization
    await require_coach_for_cohort(current_user, str(cohort_id), db)

    # Get all sessions for this cohort (via sessions-service)
    session_id_strs = await get_session_ids_for_cohort(
        str(cohort_id), calling_service="attendance"
    )
    session_ids = [uuid.UUID(sid) for sid in session_id_strs]
    total_sessions = len(session_ids)

    if total_sessions == 0:
        return CohortAttendanceSummary(
            cohort_id=cohort_id,
            total_sessions=0,
            students=[],
        )

    # Get enrolled students via academy-service
    settings = get_settings()
    enrolled_resp = await internal_get(
        service_url=settings.ACADEMY_SERVICE_URL,
        path=f"/internal/academy/cohorts/{cohort_id}/enrolled-students",
        calling_service="attendance",
    )
    if enrolled_resp.status_code != 200:
        enrolled_students = []
    else:
        enrolled_students = enrolled_resp.json()

    # Bulk-lookup member details
    enrolled_member_ids = [str(s["member_id"]) for s in enrolled_students]
    members_data = await get_members_bulk(
        enrolled_member_ids, calling_service="attendance"
    )
    members_map = {m["id"]: m for m in members_data}

    # Get attendance counts per student for this cohort's sessions (our own table)
    attendance_result = await db.execute(
        select(
            AttendanceRecord.member_id,
            func.count(AttendanceRecord.id).label("attended"),
        )
        .where(
            AttendanceRecord.session_id.in_(session_ids),
            AttendanceRecord.status == AttendanceStatus.PRESENT,
        )
        .group_by(AttendanceRecord.member_id)
    )
    attendance_counts = {
        str(row.member_id): row.attended for row in attendance_result.all()
    }

    # Build summary for each student
    student_summaries = []
    for enrollment in enrolled_students:
        mid = str(enrollment["member_id"])
        member = members_map.get(mid, {})
        attended = attendance_counts.get(mid, 0)
        student_summaries.append(
            StudentAttendanceSummary(
                member_id=enrollment["member_id"],
                member_name=f"{member.get('first_name', '')} {member.get('last_name', '')}".strip()
                or "Unknown",
                member_email=member.get("email"),
                sessions_attended=attended,
                sessions_total=total_sessions,
                attendance_rate=(
                    attended / total_sessions if total_sessions > 0 else 0.0
                ),
            )
        )

    return CohortAttendanceSummary(
        cohort_id=cohort_id,
        total_sessions=total_sessions,
        students=student_summaries,
    )


@router.get("/me", response_model=List[AttendanceResponse])
async def get_my_attendance_history(
    response: Response,
    include_session: bool = Query(
        default=True,
        description="Include batched session display details.",
    ),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a bounded attendance-history page for the current member.
    """
    started_at = time.perf_counter()
    query = (
        select(AttendanceRecord)
        .where(AttendanceRecord.member_id == current_member.id)
        .order_by(AttendanceRecord.created_at.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    db_started_at = time.perf_counter()
    result = await db.execute(query)
    rows = list(result.scalars().all())
    db_ms = (time.perf_counter() - db_started_at) * 1000
    has_more = len(rows) > limit
    records = rows[:limit]

    if not records:
        total_ms = (time.perf_counter() - started_at) * 1000
        response.headers["X-Result-Count"] = "0"
        response.headers["X-Has-More"] = "false"
        response.headers["Server-Timing"] = (
            f"attendance_db;dur={db_ms:.2f}, attendance_total;dur={total_ms:.2f}"
        )
        return []

    # One cross-service request replaces the previous per-record HTTP loop.
    session_map: dict[str, dict] = {}
    session_batch_started_at = time.perf_counter()
    if include_session:
        unique_session_ids = list(dict.fromkeys(str(r.session_id) for r in records))
        try:
            sessions = await get_sessions_by_ids(
                unique_session_ids,
                calling_service="attendance",
            )
            session_map = {str(item["id"]): item for item in sessions if item.get("id")}
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            logger.warning(
                "Attendance history session enrichment failed",
                extra={
                    "extra_fields": {
                        "member_id": str(current_member.id),
                        "session_count": len(unique_session_ids),
                        "error": str(exc),
                    }
                },
            )
    session_batch_ms = (time.perf_counter() - session_batch_started_at) * 1000

    # Build enriched response objects
    enriched: list[AttendanceResponse] = []
    for record in records:
        resp = AttendanceResponse.model_validate(record)
        session_data = session_map.get(str(record.session_id))
        if session_data:
            from services.attendance_service.schemas.main import SessionSummary

            resp.session = SessionSummary(
                id=session_data.get("id", str(record.session_id)),
                title=session_data.get("title", "Session"),
                session_type=session_data.get("session_type", ""),
                start_time=session_data.get("starts_at", ""),
                location_name=session_data.get("location_name")
                or session_data.get("location"),
            )
        enriched.append(resp)

    total_ms = (time.perf_counter() - started_at) * 1000
    response.headers["X-Result-Count"] = str(len(records))
    response.headers["X-Has-More"] = str(has_more).lower()
    if has_more:
        response.headers["X-Next-Offset"] = str(offset + limit)
    response.headers["Server-Timing"] = (
        f"attendance_db;dur={db_ms:.2f}, "
        f"session_batch;dur={session_batch_ms:.2f}, "
        f"attendance_total;dur={total_ms:.2f}"
    )
    logger.info(
        "Attendance history completed",
        extra={
            "extra_fields": {
                "member_id": str(current_member.id),
                "result_count": len(records),
                "has_more": has_more,
                "include_session": include_session,
                "db_ms": round(db_ms, 2),
                "session_batch_ms": round(session_batch_ms, 2),
                "duration_ms": round(total_ms, 2),
            }
        },
    )
    return enriched


def compute_ytd_attendance_summary(
    records: list,
    sessions_by_id: dict[str, dict],
    *,
    year: int,
    now,
) -> MemberYtdAttendanceSummary:
    """Compute all-swim YTD attendance metrics with true cross-quarter Lagos streaks."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    lagos = ZoneInfo("Africa/Lagos")
    attended_statuses = {"present", "late"}
    status_by_session: dict[str, str] = {}

    for record in records:
        role_val = (
            record.role.value if hasattr(record.role, "value") else str(record.role)
        ).lower()
        if role_val not in {"swimmer", "guest"}:
            continue
        sid = str(record.session_id)
        status_val = (
            record.status.value
            if hasattr(record.status, "value")
            else str(record.status)
        ).lower()
        existing = status_by_session.get(sid)
        if status_val in attended_statuses:
            status_by_session[sid] = status_val
        elif existing not in attended_statuses and status_val == "absent":
            status_by_session[sid] = status_val
        elif existing is None:
            status_by_session[sid] = status_val

    sessions_attended = 0
    sessions_absent = 0
    sessions_excused = 0
    pool_hours = 0.0
    attended_dates = []

    for sid, status_val in status_by_session.items():
        session = sessions_by_id.get(sid)
        if not session:
            continue
        if session.get("status") not in {"scheduled", "in_progress", "completed"}:
            continue
        raw_start = session.get("starts_at")
        if not raw_start:
            continue
        starts_at = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
        if starts_at > now:
            continue
        local_start = starts_at.astimezone(lagos)
        if local_start.year != year:
            continue

        if status_val in attended_statuses:
            sessions_attended += 1
            attended_dates.append(local_start.date())
            raw_end = session.get("ends_at")
            if raw_end:
                ends_at = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
                if ends_at > starts_at:
                    pool_hours += (ends_at - starts_at).total_seconds() / 3600.0
        elif status_val == "absent":
            sessions_absent += 1
        elif status_val == "excused":
            sessions_excused += 1

    sessions_recorded = sessions_attended + sessions_absent
    attendance_rate = (
        round(sessions_attended / sessions_recorded, 4)
        if sessions_recorded > 0
        else 0.0
    )

    week_mondays = sorted({d - timedelta(days=d.weekday()) for d in attended_dates})
    week_set = set(week_mondays)
    one_week = timedelta(weeks=1)

    streak_longest = 0
    run = 0
    prev = None
    for wk in week_mondays:
        if prev is not None and wk == prev + one_week:
            run += 1
        else:
            run = 1
        if run > streak_longest:
            streak_longest = run
        prev = wk

    today_lagos = now.astimezone(lagos).date()
    streak_current = 0
    if today_lagos.year == year:
        this_monday = today_lagos - timedelta(days=today_lagos.weekday())
        cursor = this_monday if this_monday in week_set else this_monday - one_week
        while cursor in week_set:
            streak_current += 1
            cursor -= one_week

    return MemberYtdAttendanceSummary(
        year=year,
        sessions_attended=sessions_attended,
        sessions_recorded=sessions_recorded,
        sessions_absent=sessions_absent,
        sessions_excused=sessions_excused,
        attendance_rate=attendance_rate,
        pool_hours=round(pool_hours, 1),
        streak_current=streak_current,
        streak_longest=streak_longest,
        active_weeks=len(week_mondays),
    )


@router.get("/me/ytd-summary", response_model=MemberYtdAttendanceSummary)
async def get_my_ytd_attendance_summary(
    year: int | None = Query(default=None, ge=2024, le=2035),
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """Return a single-request all-swim YTD attendance summary for the member dashboard.

    Counts all recorded swimmer sessions across Club, Academy, and Community in
    Lagos time and computes true cross-quarter weekly streaks (ADR-022).
    """
    from zoneinfo import ZoneInfo

    from libs.common.datetime_utils import utc_now

    now = utc_now()
    target_year = year or now.astimezone(ZoneInfo("Africa/Lagos")).year

    result = await db.execute(
        select(AttendanceRecord).where(
            AttendanceRecord.member_id == current_member.id,
            AttendanceRecord.role.in_([AttendanceRole.SWIMMER, AttendanceRole.GUEST]),
        )
    )
    records = list(result.scalars().all())
    if not records:
        return MemberYtdAttendanceSummary(year=target_year)

    unique_ids = list(dict.fromkeys(str(r.session_id) for r in records))
    sessions_by_id: dict[str, dict] = {}
    for offset in range(0, len(unique_ids), 200):
        batch = await get_sessions_by_ids(
            unique_ids[offset : offset + 200],
            calling_service="attendance",
        )
        for item in batch:
            if item.get("id"):
                sessions_by_id[str(item["id"])] = item

    return compute_ytd_attendance_summary(
        records, sessions_by_id, year=target_year, now=now
    )
