"""Students record their own included-class exceptions without a booking."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from libs.common.service_client import check_cohort_enrollment, get_session_by_id
from libs.db.session import get_async_db
from services.attendance_service.models import AttendanceRecord, MemberRef
from services.attendance_service.schemas import AttendanceResponse
from services.attendance_service.schemas.academy import (
    AcademyAttendanceExceptionRequest,
)
from services.attendance_service.services.academy_defaults import parse_datetime

from ._shared import get_current_member, require_academy_safety_clearance

router = APIRouter()
STUDENT_REPORT_PREFIX = "[academy_student_report:"
EXCUSE_NOTICE = timedelta(hours=24)


@router.post(
    "/sessions/{session_id}/my-academy-attendance", response_model=AttendanceResponse
)
async def report_my_academy_attendance(
    session_id: uuid.UUID,
    payload: AcademyAttendanceExceptionRequest,
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """Report an own-class exception before class ends; no separate booking needed.

    Excused absence keeps the existing 24-hour notice rule. Later absence is
    recorded as Absent. A student may undo their own report, while coach/admin
    exceptions can only be cleared by a coach/admin. This action moves no money.
    """
    session = await get_session_by_id(str(session_id), calling_service="attendance")
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if (
        session.get("session_type") != "cohort_class"
        or not session.get("cohort_id")
        or (session.get("cohort_fee_mode") or "included") != "included"
    ):
        raise HTTPException(
            status_code=422,
            detail="Use booking actions for sessions outside your included Academy classes",
        )
    now = utc_now()
    starts = parse_datetime(session.get("starts_at"))
    ends = parse_datetime(session.get("ends_at"))
    if (
        session.get("status") not in {"scheduled", "in_progress"}
        or not starts
        or not ends
        or ends <= now
    ):
        raise HTTPException(
            status_code=422,
            detail="This class has ended or is unavailable; ask your coach to correct attendance",
        )
    enrollment = await check_cohort_enrollment(
        str(session["cohort_id"]),
        str(current_member.id),
        calling_service="attendance",
    )
    if (
        not enrollment
        or not enrollment.get("enrolled")
        or enrollment.get("access_suspended")
        or enrollment.get("paused_at")
    ):
        raise HTTPException(
            status_code=403, detail="An active enrollment in this cohort is required"
        )
    if payload.status == "excused" and starts - now < EXCUSE_NOTICE:
        raise HTTPException(
            status_code=422,
            detail="Excused absence requires 24 hours' notice; report Absent or contact your coach",
        )
    if payload.status in {"present", "late"}:
        await require_academy_safety_clearance(session, [current_member.id])
    note = f"{STUDENT_REPORT_PREFIX}{now.isoformat()}]"
    if payload.notes and payload.notes.strip():
        note += f"\n{payload.notes.strip()}"
    statement = insert(AttendanceRecord).values(
        id=uuid.uuid4(),
        session_id=session_id,
        member_id=current_member.id,
        status=payload.status,
        role="swimmer",
        notes=note,
    )
    # Restoring Present must not erase an operator-recorded absence/exception,
    # including one saved concurrently with this student request.
    may_restore = or_(
        AttendanceRecord.status == "present",
        AttendanceRecord.notes.startswith(STUDENT_REPORT_PREFIX),
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_session_member_attendance",
        set_={"status": payload.status, "notes": note, "updated_at": now},
        where=may_restore if payload.status == "present" else True,
    ).returning(AttendanceRecord.id)
    record_id = await db.scalar(statement)
    if record_id is None:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Your coach/admin recorded this exception; ask them to restore Present",
        )
    await db.commit()
    row = (
        await db.scalars(
            select(AttendanceRecord)
            .where(AttendanceRecord.id == record_id)
            .execution_options(populate_existing=True)
        )
    ).one()
    return row
