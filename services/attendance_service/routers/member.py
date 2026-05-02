import uuid
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from libs.auth.dependencies import get_current_user, is_admin_or_service, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.currency import kobo_to_bubbles
from libs.common.service_client import (
    check_cohort_enrollment,
    debit_member_wallet,
    emit_rewards_event,
    get_member_by_auth_id,
    get_member_membership,
    get_members_bulk,
    get_session_by_id,
    get_session_ids_for_cohort,
    internal_get,
)
from libs.db.session import get_async_db
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.attendance_service.models import (
    AttendanceRecord,
    AttendanceStatus,
    MemberRef,
)
from services.attendance_service.schemas import (
    AttendanceCreate,
    AttendanceResponse,
    CoachAttendanceMarkRequest,
    CoachAttendanceMarkResponse,
    CohortAttendanceSummary,
    PublicAttendanceCreate,
    StudentAttendanceSummary,
)

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

    # Get the session to find its cohort_id (via sessions-service)
    session_data = await get_session_by_id(
        str(session_id), calling_service="attendance"
    )
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    cohort_id = session_data.get("cohort_id")

    # Non-cohort sessions are admin-only
    if cohort_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can view attendance for non-cohort sessions",
        )

    # Get member_id from auth_id (via members-service)
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="attendance"
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Member profile not found"
        )

    # Check if coach is assigned to this cohort (via academy-service)
    settings = get_settings()
    cohort_resp = await internal_get(
        service_url=settings.ACADEMY_SERVICE_URL,
        path=f"/internal/academy/cohorts/{cohort_id}",
        calling_service="attendance",
    )
    if cohort_resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cohort not found"
        )
    cohort_data = cohort_resp.json()

    if str(cohort_data.get("coach_id")) != str(member["id"]):
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


async def validate_session_access(
    session_data: dict,
    member_id: str,
) -> None:
    """Enforce tier-based session access control.

    Raises HTTPException with friendly messages if the member's membership
    tier does not permit access to this session type.

    Access rules:
    - cohort_class: only enrolled cohort members (not suspended)
    - club: only members with active club tier
    - community/event: any member with an active membership
    - one_on_one/group_booking: no tier check (future booking system)
    """
    from datetime import datetime, timezone

    session_type = session_data.get("session_type")

    if session_type == "cohort_class":
        cohort_id = session_data.get("cohort_id")
        if not cohort_id:
            # Cohort session without a cohort_id — shouldn't happen, allow through
            return

        enrollment = await check_cohort_enrollment(
            str(cohort_id), member_id, calling_service="attendance"
        )
        if not enrollment or not enrollment.get("enrolled"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This academy cohort follows a structured curriculum — "
                    "members start and progress together. "
                    "Check swimbuddz.com for the next cohort enrollment."
                ),
            )
        if enrollment.get("access_suspended"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Your access to this cohort is currently suspended. "
                    "Please contact the SwimBuddz team for more information."
                ),
            )

    elif session_type == "club":
        membership = await get_member_membership(
            member_id, calling_service="attendance"
        )
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This is a club session for members training together weekly. "
                    "Join the club to participate — visit swimbuddz.com for details."
                ),
            )
        active_tiers = membership.get("active_tiers") or []
        club_paid_until = membership.get("club_paid_until")

        has_club = "club" in active_tiers
        club_current = False
        if club_paid_until:
            try:
                paid_until = datetime.fromisoformat(club_paid_until)
                club_current = paid_until > datetime.now(timezone.utc)
            except (ValueError, TypeError):
                pass

        if not has_club or not club_current:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This is a club session for members training together weekly. "
                    "Join the club to participate — plans start at \u20a642,500/quarter. "
                    "Visit swimbuddz.com or ask any club member for details!"
                ),
            )

    elif session_type in ("community", "event"):
        membership = await get_member_membership(
            member_id, calling_service="attendance"
        )
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Welcome to SwimBuddz! You need an active membership to sign "
                    "in to sessions. Community membership starts at \u20a620,000/year "
                    "— visit swimbuddz.com to get started."
                ),
            )
        active_tiers = membership.get("active_tiers") or []
        if not active_tiers:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Your membership isn't currently active. "
                    "Renew at swimbuddz.com to sign in to sessions."
                ),
            )

    # one_on_one, group_booking — no tier check (future booking system)


@router.post("/sessions/{session_id}/sign-in", response_model=AttendanceResponse)
async def sign_in_to_session(
    session_id: uuid.UUID,
    attendance_in: AttendanceCreate,
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Sign in to a session. Idempotent upsert.
    When pay_with_bubbles=True the member's wallet is debited for the session fee
    (only on the first sign-in, not on subsequent upserts).
    """
    # Verify session exists (via sessions-service)
    session_data = await get_session_by_id(
        str(session_id), calling_service="attendance"
    )
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")

    # Enforce tier-based access control (admins/coaches skip this check
    # since they need to mark attendance for any session)
    await validate_session_access(session_data, str(current_member.id))

    # Check for existing attendance
    query = select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.member_id == current_member.id,
    )
    result = await db.execute(query)
    attendance = result.scalar_one_or_none()
    is_new = attendance is None

    wallet_txn_id = None

    # Debit wallet on first sign-in when requested and session has a fee
    if (
        is_new
        and attendance_in.pay_with_bubbles
        and attendance_in.status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE)
    ):
        pool_fee_kobo = session_data.get("pool_fee") or 0
        if pool_fee_kobo > 0:
            fee_bubbles = kobo_to_bubbles(pool_fee_kobo)
            idempotency_key = f"session-fee-{session_id}-{current_member.id}"
            try:
                result_txn = await debit_member_wallet(
                    current_member.auth_id,
                    amount=fee_bubbles,
                    idempotency_key=idempotency_key,
                    description=f"Session fee — {session_data.get('title', '')} ({fee_bubbles} 🫧)",
                    calling_service="attendance",
                    transaction_type="purchase",
                    reference_type="session",
                    reference_id=str(session_id),
                )
                wallet_txn_id = result_txn.get("transaction_id")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400:
                    detail = e.response.json().get("detail", "")
                    if "Insufficient" in detail:
                        raise HTTPException(
                            status_code=402,
                            detail="Insufficient Bubbles. Please top up your wallet.",
                        )
                    if "frozen" in detail.lower() or "suspended" in detail.lower():
                        raise HTTPException(
                            status_code=403,
                            detail="Wallet is inactive. Please contact support.",
                        )
                raise

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
            wallet_transaction_id=wallet_txn_id,
        )
        db.add(attendance)

    await db.commit()
    await db.refresh(attendance)

    # Best-effort: check attendance milestones after new sign-in
    if is_new and attendance_in.status in (
        AttendanceStatus.PRESENT,
        AttendanceStatus.LATE,
    ):
        await _check_attendance_milestones(
            db, current_member.id, current_member.auth_id
        )

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
    # Verify session exists (via sessions-service)
    session_data = await get_session_by_id(
        str(session_id), calling_service="attendance"
    )
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify member exists
    query = select(MemberRef).where(MemberRef.id == attendance_in.member_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Enforce tier-based access control
    await validate_session_access(session_data, str(attendance_in.member_id))

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


@router.post(
    "/sessions/{session_id}/coach-mark",
    response_model=CoachAttendanceMarkResponse,
)
async def coach_mark_session_attendance(
    session_id: uuid.UUID,
    payload: CoachAttendanceMarkRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Bulk attendance mark by the cohort's coach (or admin override).

    Default-present model: students NOT included in `entries` are treated
    as PRESENT — no row is written, the cohort payout calculator infers
    presence from the absence of an exception. The coach typically only
    submits entries for EXCUSED, ABSENT, or LATE statuses.

    Behavior per entry:
      - status == PRESENT: deletes any existing exception row (revert to default)
      - status in {EXCUSED, ABSENT, LATE, CANCELLED}: upserts the row by
        (session_id, member_id), overwriting prior status

    EXCUSED entries auto-create a CohortMakeupObligation downstream when
    the next coach-payout cron runs (handled by payments_service).
    """
    # Auth: admin or assigned coach
    await require_admin_or_coach_for_session(session_id, current_user, db)

    if not payload.entries:
        return CoachAttendanceMarkResponse(
            session_id=session_id, upserted=0, deleted=0, records=[]
        )

    # Resolve session → cohort_id; verify each member is enrolled in the cohort.
    session_data = await get_session_by_id(
        str(session_id), calling_service="attendance"
    )
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")
    cohort_id = session_data.get("cohort_id")
    if cohort_id is None:
        raise HTTPException(
            status_code=400,
            detail="Coach attendance marking only supported for cohort sessions",
        )

    # Pull existing rows for this session for the members in the payload.
    member_ids = [e.member_id for e in payload.entries]
    existing_rows = (
        (
            await db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.session_id == session_id,
                    AttendanceRecord.member_id.in_(member_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_member: dict[uuid.UUID, AttendanceRecord] = {
        r.member_id: r for r in existing_rows
    }

    upserted = 0
    deleted = 0

    for entry in payload.entries:
        existing = existing_by_member.get(entry.member_id)
        if entry.status == AttendanceStatus.PRESENT:
            # Revert to default-present: delete the exception row if any.
            if existing is not None:
                await db.delete(existing)
                deleted += 1
            continue

        if existing is None:
            db.add(
                AttendanceRecord(
                    session_id=session_id,
                    member_id=entry.member_id,
                    status=entry.status,
                    notes=entry.notes,
                )
            )
        else:
            existing.status = entry.status
            if entry.notes is not None:
                existing.notes = entry.notes
        upserted += 1

    await db.commit()

    # Re-fetch the resulting state for the response.
    refreshed = (
        (
            await db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.session_id == session_id,
                    AttendanceRecord.member_id.in_(member_ids),
                )
            )
        )
        .scalars()
        .all()
    )

    members_data = await get_members_bulk(
        [str(r.member_id) for r in refreshed], calling_service="attendance"
    )
    members_map = {m["id"]: m for m in members_data}

    records = []
    for record in refreshed:
        resp = AttendanceResponse.model_validate(record)
        m = members_map.get(str(record.member_id), {})
        resp.member_name = (
            f"{m.get('first_name', '')} {m.get('last_name', '')}".strip() or None
        )
        resp.member_email = m.get("email")
        records.append(resp)

    return CoachAttendanceMarkResponse(
        session_id=session_id,
        upserted=upserted,
        deleted=deleted,
        records=records,
    )


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


@router.get("/sessions/{session_id}/pool-list")
async def get_pool_list_csv(
    session_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Export pool list as CSV (Admin only).
    """
    # Get attendance records
    query = select(AttendanceRecord).where(AttendanceRecord.session_id == session_id)
    result = await db.execute(query)
    records = result.scalars().all()

    # Bulk-lookup member details
    pool_member_ids = list({str(r.member_id) for r in records})
    pool_members = await get_members_bulk(pool_member_ids, calling_service="attendance")
    pool_members_map = {m["id"]: m for m in pool_members}

    # Simple CSV generation
    csv_content = "First Name,Last Name,Email,Notes\n"
    for attendance in records:
        member = pool_members_map.get(str(attendance.member_id), {})
        csv_content += f"{member.get('first_name', '')},{member.get('last_name', '')},{member.get('email', '')},{attendance.notes or ''}\n"

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


# ---------------------------------------------------------------------------
# Attendance milestone/streak helpers
# ---------------------------------------------------------------------------


async def _check_attendance_milestones(
    db: AsyncSession,
    member_id: uuid.UUID,
    member_auth_id: str,
) -> None:
    """Best-effort: check and emit attendance milestone/streak reward events.

    Called after each new PRESENT/LATE check-in.  Counts sessions in the
    current calendar month and emits ``attendance.monthly_milestone`` when
    thresholds (4, 8+) are first crossed.  Also checks consecutive-week
    streaks and emits ``attendance.streak`` at 4-week marks.
    """
    from datetime import datetime, timezone

    try:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Count sessions attended this month
        month_count_result = await db.execute(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.member_id == member_id,
                AttendanceRecord.status.in_(
                    [
                        AttendanceStatus.PRESENT,
                        AttendanceStatus.LATE,
                    ]
                ),
                AttendanceRecord.created_at >= month_start,
            )
        )
        session_count = month_count_result.scalar_one() or 0

        # Emit monthly milestone at threshold crossings (4 and 8)
        if session_count in (4, 8):
            month_key = now.strftime("%Y-%m")
            await emit_rewards_event(
                event_type="attendance.monthly_milestone",
                member_auth_id=member_auth_id,
                service_source="attendance",
                event_data={
                    "session_count": session_count,
                    "sessions": session_count,
                    "month": month_key,
                },
                idempotency_key=f"attendance-monthly-{member_auth_id}-{month_key}-{session_count}",
                calling_service="attendance",
            )

        # Check consecutive-week streak (simplified: count distinct weeks
        # with at least one PRESENT in the last 5 weeks)
        from datetime import timedelta

        five_weeks_ago = now - timedelta(weeks=5)
        week_result = await db.execute(
            select(
                func.count(
                    func.distinct(func.date_trunc("week", AttendanceRecord.created_at))
                )
            ).where(
                AttendanceRecord.member_id == member_id,
                AttendanceRecord.status.in_(
                    [
                        AttendanceStatus.PRESENT,
                        AttendanceStatus.LATE,
                    ]
                ),
                AttendanceRecord.created_at >= five_weeks_ago,
            )
        )
        consecutive_weeks = week_result.scalar_one() or 0

        # Emit streak reward at 4-week mark
        if consecutive_weeks >= 4:
            await emit_rewards_event(
                event_type="attendance.streak",
                member_auth_id=member_auth_id,
                service_source="attendance",
                event_data={
                    "consecutive_weeks": consecutive_weeks,
                    "streak_weeks": consecutive_weeks,
                },
                idempotency_key=f"attendance-streak-{member_auth_id}-{consecutive_weeks}w",
                calling_service="attendance",
            )
    except Exception:
        # Best-effort — never block the sign-in flow
        pass
