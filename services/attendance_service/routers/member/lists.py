"""Listing endpoints: session attendance, cohort summary, my history."""

import uuid
from typing import List

from fastapi import APIRouter, Depends
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.service_client import (
    get_members_bulk,
    get_session_by_id,
    get_session_ids_for_cohort,
    internal_get,
)
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.attendance_service.models import AttendanceRecord, AttendanceStatus, MemberRef
from services.attendance_service.schemas import (
    AttendanceResponse,
    CohortAttendanceSummary,
    StudentAttendanceSummary,
)

from ._shared import get_current_member, require_admin_or_coach_for_session

router = APIRouter(tags=["attendance"])


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
    member_ids = list({str(r.member_id) for r in records})
    members_data = await get_members_bulk(member_ids, calling_service="attendance")
    members_map = {m["id"]: m for m in members_data}

    responses = []
    for attendance in records:
        resp = AttendanceResponse.model_validate(attendance)
        member = members_map.get(str(attendance.member_id), {})
        resp.member_name = (
            f"{member.get('first_name', '')} {member.get('last_name', '')}".strip()
            or None
        )
        resp.member_email = member.get("email")
        responses.append(resp)

    return responses


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
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get attendance history for the current member, enriched with session details.
    """
    query = (
        select(AttendanceRecord)
        .where(AttendanceRecord.member_id == current_member.id)
        .order_by(AttendanceRecord.created_at.desc())
    )
    result = await db.execute(query)
    records = result.scalars().all()

    if not records:
        return []

    # Collect unique session IDs and fetch session details in parallel
    unique_session_ids = list({str(r.session_id) for r in records})
    session_map: dict[str, dict] = {}
    for sid in unique_session_ids:
        try:
            session_data = await get_session_by_id(sid, calling_service="attendance")
            if session_data:
                session_map[sid] = session_data
        except Exception:
            pass  # Best-effort — if session lookup fails, skip enrichment

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

    return enriched
