import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from libs.auth.dependencies import get_current_user, is_admin_or_service, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.db.session import get_async_db
from services.attendance_service.models import AttendanceRecord, MemberRef
from services.attendance_service.schemas import (
    AttendanceCreate,
    AttendanceResponse,
    CohortAttendanceSummary,
    PublicAttendanceCreate,
    StudentAttendanceSummary,
)
from services.members_service.models import Member
from services.sessions_service.models import Session
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["attendance"])
settings = get_settings()


async def require_admin_or_coach_for_session(
    session_id: uuid.UUID,
    current_user: AuthUser,
    db: AsyncSession,
) -> None:
    """
    Verify the user is either an admin or the coach assigned to the session's cohort.
    Raises 403 if not authorized.

    For cohort sessions: checks if user is the cohort's coach
    For non-cohort sessions: only admins allowed
    """
    # Admins and service roles can access any session
    if is_admin_or_service(current_user):
        return

    # Must have coach role
    if not current_user.has_role("coach"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or coach privileges required",
        )

    # Get the session to find its cohort_id
    session_result = await db.execute(
        select(Session.cohort_id).where(Session.id == session_id)
    )
    session_row = session_result.first()

    if not session_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    cohort_id = session_row[0]

    # Non-cohort sessions are admin-only
    if cohort_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can view attendance for non-cohort sessions",
        )

    # Get member_id from auth_id
    member_result = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": current_user.user_id},
    )
    member_row = member_result.mappings().first()

    if not member_row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Member profile not found"
        )

    # Check if coach is assigned to this cohort
    cohort_result = await db.execute(
        text("SELECT coach_id FROM cohorts WHERE id = :cohort_id"),
        {"cohort_id": str(cohort_id)},
    )
    cohort_row = cohort_result.mappings().first()

    if not cohort_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cohort not found"
        )

    if str(cohort_row["coach_id"]) != str(member_row["id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the assigned coach for this cohort",
        )


async def get_current_member(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> MemberRef:
    query = select(MemberRef).where(MemberRef.auth_id == current_user.user_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )
    return member


@router.post("/sessions/{session_id}/sign-in", response_model=AttendanceResponse)
async def sign_in_to_session(
    session_id: uuid.UUID,
    attendance_in: AttendanceCreate,
    current_member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Sign in to a session. Idempotent upsert.
    """
    # Verify session exists
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check for existing attendance
    query = select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.member_id == current_member.id,
    )
    result = await db.execute(query)
    attendance = result.scalar_one_or_none()

    if attendance:
        # Update existing
        attendance.status = attendance_in.status
        attendance.role = attendance_in.role
        attendance.notes = attendance_in.notes
    else:
        # Create new
        attendance = AttendanceRecord(
            session_id=session_id,
            member_id=current_member.id,
            status=attendance_in.status,
            role=attendance_in.role,
            notes=attendance_in.notes,
        )
        db.add(attendance)

    await db.commit()
    await db.refresh(attendance)
    return attendance


@router.post(
    "/sessions/{session_id}/attendance/public", response_model=AttendanceResponse
)
async def public_sign_in_to_session(
    session_id: uuid.UUID,
    attendance_in: PublicAttendanceCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Public sign in to a session (no auth required). Idempotent upsert.
    """
    # Verify session exists
    query = select(Session).where(Session.id == session_id)
    result = await db.execute(query)
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify member exists
    query = select(MemberRef).where(MemberRef.id == attendance_in.member_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Check for existing attendance
    query = select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.member_id == attendance_in.member_id,
    )
    result = await db.execute(query)
    attendance = result.scalar_one_or_none()

    if attendance:
        # Update existing
        attendance.status = attendance_in.status
        attendance.role = attendance_in.role
        attendance.notes = attendance_in.notes
    else:
        # Create new
        attendance = AttendanceRecord(
            session_id=session_id,
            member_id=attendance_in.member_id,
            status=attendance_in.status,
            role=attendance_in.role,
            notes=attendance_in.notes,
        )
        db.add(attendance)

    await db.commit()
    await db.refresh(attendance)
    return attendance


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

    query = (
        select(AttendanceRecord, Member)
        .select_from(AttendanceRecord)
        .join(Member, Member.id == AttendanceRecord.member_id)
        .where(AttendanceRecord.session_id == session_id)
    )
    result = await db.execute(query)
    rows = result.all()

    responses = []
    for attendance, member in rows:
        # Convert SQLAlchemy model to Pydantic model
        resp = AttendanceResponse.model_validate(attendance)
        # Manually populate extra fields
        resp.member_name = f"{member.first_name} {member.last_name}"
        resp.member_email = member.email
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

    # Get all sessions for this cohort
    sessions_result = await db.execute(
        text(
            """
            SELECT id FROM sessions
            WHERE cohort_id = :cohort_id
            ORDER BY starts_at
            """
        ),
        {"cohort_id": str(cohort_id)},
    )
    session_ids = [row[0] for row in sessions_result.fetchall()]
    total_sessions = len(session_ids)

    if total_sessions == 0:
        return CohortAttendanceSummary(
            cohort_id=cohort_id,
            total_sessions=0,
            students=[],
        )

    # Get all enrolled students in this cohort
    enrollments_result = await db.execute(
        text(
            """
            SELECT e.member_id, m.first_name, m.last_name, m.email
            FROM enrollments e
            JOIN members m ON e.member_id = m.id
            WHERE e.cohort_id = :cohort_id
            AND e.status IN ('CONFIRMED', 'COMPLETED')
            """
        ),
        {"cohort_id": str(cohort_id)},
    )
    students = enrollments_result.mappings().fetchall()

    # Get attendance counts per student for this cohort's sessions
    attendance_result = await db.execute(
        text(
            """
            SELECT member_id, COUNT(*) as attended
            FROM attendance_records
            WHERE session_id = ANY(:session_ids)
            AND status = 'PRESENT'
            GROUP BY member_id
            """
        ),
        {"session_ids": session_ids},
    )
    attendance_counts = {
        row["member_id"]: row["attended"]
        for row in attendance_result.mappings().fetchall()
    }

    # Build summary for each student
    student_summaries = []
    for student in students:
        attended = attendance_counts.get(student["member_id"], 0)
        student_summaries.append(
            StudentAttendanceSummary(
                member_id=student["member_id"],
                member_name=f"{student['first_name']} {student['last_name']}",
                member_email=student["email"],
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


@router.get("/me/attendance", response_model=List[AttendanceResponse])
async def get_my_attendance_history(
    current_member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get attendance history for the current member.
    """
    query = (
        select(AttendanceRecord)
        .where(AttendanceRecord.member_id == current_member.id)
        .order_by(AttendanceRecord.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/sessions/{session_id}/pool-list")
async def get_pool_list_csv(
    session_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Export pool list as CSV (Admin only).
    """
    # Join with Member to get names
    query = (
        select(AttendanceRecord, Member)
        .select_from(AttendanceRecord)
        .join(Member, Member.id == AttendanceRecord.member_id)
        .where(AttendanceRecord.session_id == session_id)
    )
    result = await db.execute(query)
    rows = result.all()

    # Simple CSV generation (status/role removed per request)
    csv_content = "First Name,Last Name,Email,Notes\n"
    for attendance, member in rows:
        csv_content += f"{member.first_name},{member.last_name},{member.email},{attendance.notes or ''}\n"

    return Response(content=csv_content, media_type="text/csv")


@router.delete("/admin/members/{member_id}")
async def admin_delete_member_attendance(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete attendance records for a member (Admin only).
    """
    result = await db.execute(
        delete(AttendanceRecord).where(AttendanceRecord.member_id == member_id)
    )
    await db.commit()
    return {"deleted": result.rowcount or 0}
