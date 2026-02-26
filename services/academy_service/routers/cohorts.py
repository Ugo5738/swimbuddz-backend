from fastapi import APIRouter
from services.academy_service.routers._shared import (
    AsyncSession,
    AuthUser,
    CoachAssignment,
    Cohort,
    CohortCreate,
    CohortResource,
    CohortResourceResponse,
    CohortResponse,
    CohortStatus,
    CohortTimelineSessionImpact,
    CohortTimelineShiftApplyResponse,
    CohortTimelineShiftLog,
    CohortTimelineShiftLogResponse,
    CohortTimelineShiftPreviewResponse,
    CohortTimelineShiftRequest,
    CohortUpdate,
    Depends,
    Enrollment,
    EnrollmentInstallment,
    EnrollmentResponse,
    EnrollmentStatus,
    HTTPException,
    InstallmentStatus,
    IntegrityError,
    List,
    Program,
    _COHORT_TIMELINE_NOTIFY_STATUSES,
    _START_COUNTDOWN_REMINDER_KEYS,
    _build_session_impacts,
    _build_shift_notice_body,
    _ensure_active_coach,
    _fetch_cohort_sessions_for_shift,
    _is_mid_entry_open_now,
    _shift_sessions_or_raise,
    _sync_installment_state_for_enrollment,
    _timeline_shift_response_from_log,
    _to_utc,
    _updated_at_mismatch,
    _validate_shift_window,
    and_,
    asyncio,
    func,
    get_async_db,
    get_current_user,
    get_email_client,
    get_logger,
    get_member_by_auth_id,
    get_member_by_id,
    get_members_bulk,
    get_settings,
    internal_delete,
    joinedload,
    or_,
    require_admin,
    require_coach,
    require_coach_for_cohort,
    select,
    selectinload,
    status,
    utc_now,
    uuid,
)

router = APIRouter(tags=["academy"])
logger = get_logger(__name__)


# --- Cohorts ---


@router.post("/cohorts", response_model=CohortResponse)
async def create_cohort(
    cohort_in: CohortCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    # Validate all coaches listed in assignments are active
    for ca in cohort_in.coach_assignments or []:
        await _ensure_active_coach(ca.coach_id)

    # Extract coach_assignments before creating cohort (not a DB field)
    coach_assignments_input = cohort_in.coach_assignments
    cohort_data = cohort_in.model_dump(exclude={"coach_assignments"})
    cohort = Cohort(**cohort_data)
    db.add(cohort)
    await db.flush()  # Get cohort.id before creating assignments

    # Get admin member ID for assigned_by_id
    admin_member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    admin_id = admin_member["id"] if admin_member else None

    # Create CoachAssignment records
    for ca_input in coach_assignments_input or []:
        assignment = CoachAssignment(
            cohort_id=cohort.id,
            coach_id=ca_input.coach_id,
            role=ca_input.role,
            assigned_by_id=admin_id,
            status="active",
        )
        db.add(assignment)

        # Keep cohort.coach_id denormalised to the lead coach for fast lookups
        if ca_input.role == "lead":
            cohort.coach_id = ca_input.coach_id

    await db.commit()

    query = (
        select(Cohort)
        .where(Cohort.id == cohort.id)
        .options(selectinload(Cohort.program))
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.put("/cohorts/{cohort_id}", response_model=CohortResponse)
async def update_cohort(
    cohort_id: uuid.UUID,
    cohort_in: CohortUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = (
        select(Cohort)
        .where(Cohort.id == cohort_id)
        .options(selectinload(Cohort.program))
    )
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()

    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    update_data = cohort_in.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(cohort, field, value)

    await db.commit()

    query = (
        select(Cohort)
        .where(Cohort.id == cohort.id)
        .options(selectinload(Cohort.program))
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.delete("/cohorts/{cohort_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cohort(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Cohort).where(Cohort.id == cohort_id)
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()

    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    # Clean up sessions via sessions-service (cross-service, no FK cascade).

    settings = get_settings()
    resp = await internal_delete(
        service_url=settings.SESSIONS_SERVICE_URL,
        path=f"/sessions/by-cohort/{cohort_id}",
        calling_service="academy",
        timeout=15,
    )
    if not resp.is_success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to delete cohort sessions",
        )

    # DB cascades handle: enrollments → student_progress, cohort_resources,
    # cohort_complexity_scores, coach_assignments (all have ondelete="CASCADE").
    await db.delete(cohort)
    await db.commit()
    return None


@router.get("/cohorts", response_model=List[CohortResponse])
async def list_cohorts(
    program_id: uuid.UUID = None,
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Cohort).order_by(Cohort.start_date.desc())
    if program_id:
        query = query.where(Cohort.program_id == program_id)
    query = query.options(selectinload(Cohort.program))

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/cohorts/open", response_model=List[CohortResponse])
async def list_open_cohorts(
    db: AsyncSession = Depends(get_async_db),
):
    """List all cohorts with status OPEN, only from published programs."""

    query = (
        select(Cohort)
        .join(Program, Cohort.program_id == Program.id)
        .where(Cohort.status == CohortStatus.OPEN)
        .where(
            Program.is_published.is_(True)
        )  # Only show cohorts from published programs
        .options(selectinload(Cohort.program))
        .order_by(Cohort.start_date.asc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/cohorts/enrollable", response_model=List[CohortResponse])
async def list_enrollable_cohorts(
    program_id: uuid.UUID = None,
    db: AsyncSession = Depends(get_async_db),
):
    """List cohorts members can enroll in right now.

    Includes:
    - OPEN cohorts (published programs)
    - ACTIVE cohorts where mid-entry is enabled and still within cutoff week
    """

    now = utc_now()

    query = (
        select(Cohort)
        .join(Program, Cohort.program_id == Program.id)
        .where(Program.is_published.is_(True))
        .where(
            or_(
                Cohort.status == CohortStatus.OPEN,
                and_(
                    Cohort.status == CohortStatus.ACTIVE,
                    Cohort.allow_mid_entry.is_(True),
                ),
            )
        )
        .options(selectinload(Cohort.program))
        .order_by(Cohort.start_date.asc())
    )

    if program_id:
        query = query.where(Cohort.program_id == program_id)

    result = await db.execute(query)
    cohorts = result.scalars().all()

    return [
        cohort
        for cohort in cohorts
        if cohort.status == CohortStatus.OPEN or _is_mid_entry_open_now(cohort, now)
    ]


@router.get("/cohorts/by-coach/{coach_member_id}", response_model=List[CohortResponse])
async def list_cohorts_by_coach(
    coach_member_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get all cohorts (current and past) taught by a specific coach.
    Public endpoint - no authentication required.
    Returns cohorts with program details.
    """
    query = (
        select(Cohort)
        .join(Program, Cohort.program_id == Program.id)
        .where(Cohort.coach_id == coach_member_id)
        .where(Program.is_published.is_(True))  # Only from published programs
        .options(selectinload(Cohort.program))
        .order_by(Cohort.start_date.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/cohorts/coach/me", response_model=List[CohortResponse])
async def list_my_coach_cohorts(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List cohorts where the current user is the assigned coach."""

    # 1. Resolve Member ID via members-service
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    # 2. Query Cohorts
    query = (
        select(Cohort)
        .where(Cohort.coach_id == uuid.UUID(member["id"]))
        .options(selectinload(Cohort.program))
        .order_by(Cohort.start_date.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/cohorts/{cohort_id}", response_model=CohortResponse)
async def get_cohort(
    cohort_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    query = (
        select(Cohort)
        .where(Cohort.id == cohort_id)
        .options(selectinload(Cohort.program))
    )
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    return cohort


@router.get("/cohorts/{cohort_id}/enrollment-stats")
async def get_cohort_enrollment_stats(
    cohort_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get enrollment statistics for a cohort (capacity, enrolled, waitlist)."""

    # Verify cohort exists
    cohort_query = select(Cohort).where(Cohort.id == cohort_id)
    cohort_result = await db.execute(cohort_query)
    cohort = cohort_result.scalar_one_or_none()

    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    # Count enrolled (includes ENROLLED and PENDING_APPROVAL)
    enrolled_result = await db.execute(
        select(func.count(Enrollment.id)).where(
            Enrollment.cohort_id == cohort_id,
            Enrollment.status.in_(
                [EnrollmentStatus.ENROLLED, EnrollmentStatus.PENDING_APPROVAL]
            ),
        )
    )
    enrolled_count = enrolled_result.scalar() or 0

    # Count waitlist
    waitlist_result = await db.execute(
        select(func.count(Enrollment.id)).where(
            Enrollment.cohort_id == cohort_id,
            Enrollment.status == EnrollmentStatus.WAITLIST,
        )
    )
    waitlist_count = waitlist_result.scalar() or 0

    spots_remaining = max(0, cohort.capacity - enrolled_count)
    is_at_capacity = enrolled_count >= cohort.capacity

    return {
        "cohort_id": str(cohort_id),
        "capacity": cohort.capacity,
        "enrolled_count": enrolled_count,
        "waitlist_count": waitlist_count,
        "spots_remaining": spots_remaining,
        "is_at_capacity": is_at_capacity,
    }


@router.get("/cohorts/{cohort_id}/students", response_model=List[EnrollmentResponse])
async def list_cohort_students(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_coach),  # Coach or Admin
    db: AsyncSession = Depends(get_async_db),
):
    """List all students enrolled in a cohort with their progress.

    Accessible by:
    - Admins (can view any cohort)
    - Coaches (can only view their assigned cohorts)
    """
    # Verify coach has access to this specific cohort
    await require_coach_for_cohort(current_user, str(cohort_id), db)

    # Eager load progress records, cohort, and program
    query = (
        select(Enrollment)
        .where(Enrollment.cohort_id == cohort_id)
        .options(
            selectinload(Enrollment.progress_records),
            joinedload(Enrollment.cohort).joinedload(Cohort.program),
            joinedload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    result = await db.execute(query)
    enrollments = result.unique().scalars().all()
    for enrollment in enrollments:
        await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()

    # Enrich with member names from members service
    enriched = []
    for enrollment in enrollments:
        data = EnrollmentResponse.model_validate(enrollment)
        try:
            member_data = await get_member_by_id(
                str(enrollment.member_id), calling_service="academy"
            )
            if member_data:
                first_name = member_data.get("first_name", "")
                last_name = member_data.get("last_name", "")
                data.member_name = f"{first_name} {last_name}".strip() or None
                data.member_email = member_data.get("email")
        except Exception:
            pass  # Gracefully degrade if members service is unavailable
        enriched.append(data)

    return enriched


@router.post(
    "/cohorts/{cohort_id}/timeline-shifts/preview",
    response_model=CohortTimelineShiftPreviewResponse,
)
async def preview_cohort_timeline_shift(
    cohort_id: uuid.UUID,
    shift_in: CohortTimelineShiftRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Preview a cohort timeline shift without applying changes.

    This endpoint is intentionally side-effect free and reports:
    - date delta validation
    - session shiftability breakdown
    - pending installment count that would be rebased
    - reminder reset opportunities
    """
    query = select(Cohort).where(Cohort.id == cohort_id)
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    new_start_utc = _to_utc(shift_in.new_start_date)
    new_end_utc = _to_utc(shift_in.new_end_date)
    delta = _validate_shift_window(
        old_start=cohort.start_date,
        old_end=cohort.end_date,
        new_start=new_start_utc,
        new_end=new_end_utc,
    )
    already_applied = (
        _to_utc(cohort.start_date) == new_start_utc
        and _to_utc(cohort.end_date) == new_end_utc
    )
    if (
        _updated_at_mismatch(cohort.updated_at, shift_in.expected_updated_at)
        and not already_applied
    ):
        raise HTTPException(
            status_code=409,
            detail="Cohort was updated by another change. Refresh and retry.",
        )

    sessions: list[dict] = []
    impacts: list[CohortTimelineSessionImpact] = []
    shiftable = 0
    blocked = 0
    if shift_in.shift_sessions and not already_applied:
        sessions = await _fetch_cohort_sessions_for_shift(cohort_id)
        impacts, shiftable, blocked = _build_session_impacts(sessions, delta)

    pending_installments = 0
    if shift_in.shift_installments and not already_applied:
        pending_installments_result = await db.execute(
            select(func.count(EnrollmentInstallment.id))
            .join(Enrollment, Enrollment.id == EnrollmentInstallment.enrollment_id)
            .where(Enrollment.cohort_id == cohort_id)
            .where(EnrollmentInstallment.status == InstallmentStatus.PENDING)
        )
        pending_installments = pending_installments_result.scalar() or 0

    reminder_resets_possible = 0
    if shift_in.reset_start_reminders and not already_applied:
        enrollments_result = await db.execute(
            select(Enrollment).where(
                Enrollment.cohort_id == cohort_id,
                Enrollment.status.in_(_COHORT_TIMELINE_NOTIFY_STATUSES),
            )
        )
        enrollments = enrollments_result.scalars().all()
        reminder_resets_possible = sum(
            1
            for enrollment in enrollments
            if set(enrollment.reminders_sent or []).intersection(
                _START_COUNTDOWN_REMINDER_KEYS
            )
        )

    return CohortTimelineShiftPreviewResponse(
        cohort_id=cohort_id,
        old_start_date=cohort.start_date,
        old_end_date=cohort.end_date,
        new_start_date=new_start_utc,
        new_end_date=new_end_utc,
        delta_seconds=int(delta.total_seconds()),
        already_applied=already_applied,
        sessions_total=len(sessions),
        sessions_shiftable=shiftable,
        sessions_blocked=blocked,
        pending_installments=pending_installments,
        reminder_resets_possible=reminder_resets_possible,
        session_impacts=impacts,
    )


@router.post(
    "/cohorts/{cohort_id}/timeline-shifts",
    response_model=CohortTimelineShiftApplyResponse,
)
async def apply_cohort_timeline_shift(
    cohort_id: uuid.UUID,
    shift_in: CohortTimelineShiftRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Apply a cohort timeline shift and propagate it across linked records.

    Workflow:
    1. Validate equal start/end delta (duration preserved)
    2. Shift eligible sessions in sessions-service (with compensation on failure)
    3. Shift pending installment due dates
    4. Reset enrollment countdown reminders
    5. Persist cohort dates and send member notifications (best effort)
    """
    # Serialize timeline-shift operations per cohort to avoid concurrent
    # double-apply races from duplicate submits/retries.
    query = select(Cohort).where(Cohort.id == cohort_id).with_for_update()
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    idempotency_key = (
        shift_in.idempotency_key.strip() if shift_in.idempotency_key else None
    )
    if idempotency_key:
        existing_log_result = await db.execute(
            select(CohortTimelineShiftLog).where(
                CohortTimelineShiftLog.cohort_id == cohort_id,
                CohortTimelineShiftLog.idempotency_key == idempotency_key,
            )
        )
        existing_log = existing_log_result.scalar_one_or_none()
        if existing_log:
            return _timeline_shift_response_from_log(existing_log)

    if cohort.status in {CohortStatus.COMPLETED, CohortStatus.CANCELLED}:
        raise HTTPException(
            status_code=400,
            detail="Cannot timeline-shift completed or cancelled cohorts",
        )

    new_start_utc = _to_utc(shift_in.new_start_date)
    new_end_utc = _to_utc(shift_in.new_end_date)
    delta = _validate_shift_window(
        old_start=cohort.start_date,
        old_end=cohort.end_date,
        new_start=new_start_utc,
        new_end=new_end_utc,
    )
    old_start = cohort.start_date
    old_end = cohort.end_date

    already_applied = (
        _to_utc(old_start) == new_start_utc and _to_utc(old_end) == new_end_utc
    )
    if (
        _updated_at_mismatch(cohort.updated_at, shift_in.expected_updated_at)
        and not already_applied
    ):
        raise HTTPException(
            status_code=409,
            detail="Cohort was updated by another change. Refresh and retry.",
        )
    if already_applied:
        response = CohortTimelineShiftApplyResponse(
            cohort_id=cohort_id,
            old_start_date=old_start,
            old_end_date=old_end,
            new_start_date=new_start_utc,
            new_end_date=new_end_utc,
            delta_seconds=int(delta.total_seconds()),
            already_applied=True,
        )
        if idempotency_key:
            log_row = CohortTimelineShiftLog(
                cohort_id=cohort_id,
                idempotency_key=idempotency_key,
                actor_auth_id=current_user.user_id,
                reason=shift_in.reason,
                old_start_date=old_start,
                old_end_date=old_end,
                new_start_date=new_start_utc,
                new_end_date=new_end_utc,
                delta_seconds=int(delta.total_seconds()),
                options_json={
                    "shift_sessions": bool(shift_in.shift_sessions),
                    "shift_installments": bool(shift_in.shift_installments),
                    "reset_start_reminders": bool(shift_in.reset_start_reminders),
                    "notify_members": bool(shift_in.notify_members),
                    "set_status_to_open_if_future": bool(
                        shift_in.set_status_to_open_if_future
                    ),
                },
                results_json={"already_applied": True},
                warnings=[],
            )
            db.add(log_row)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                existing_log_result = await db.execute(
                    select(CohortTimelineShiftLog).where(
                        CohortTimelineShiftLog.cohort_id == cohort_id,
                        CohortTimelineShiftLog.idempotency_key == idempotency_key,
                    )
                )
                existing_log = existing_log_result.scalar_one_or_none()
                if existing_log:
                    return _timeline_shift_response_from_log(existing_log)
        return response

    session_impacts: list[CohortTimelineSessionImpact] = []
    sessions_shifted = 0
    sessions_skipped = 0
    warnings: list[str] = []
    if shift_in.shift_sessions:
        sessions = await _fetch_cohort_sessions_for_shift(cohort_id)
        session_impacts, _, _ = _build_session_impacts(sessions, delta)
        (
            sessions_shifted,
            sessions_skipped,
            session_warnings,
        ) = await _shift_sessions_or_raise(impacts=session_impacts)
        warnings.extend(session_warnings)

    pending_installments_shifted = 0
    reminder_resets_applied = 0
    notify_enrollments: list[Enrollment] = []

    cohort.start_date = new_start_utc
    cohort.end_date = new_end_utc

    now = utc_now()
    if (
        shift_in.set_status_to_open_if_future
        and new_start_utc > now
        and cohort.status == CohortStatus.ACTIVE
    ):
        cohort.status = CohortStatus.OPEN

    if shift_in.shift_installments:
        pending_installments_result = await db.execute(
            select(EnrollmentInstallment)
            .join(Enrollment, Enrollment.id == EnrollmentInstallment.enrollment_id)
            .where(Enrollment.cohort_id == cohort_id)
            .where(EnrollmentInstallment.status == InstallmentStatus.PENDING)
        )
        pending_installments = pending_installments_result.scalars().all()
        for installment in pending_installments:
            installment.due_at = installment.due_at + delta
        pending_installments_shifted = len(pending_installments)

    if shift_in.reset_start_reminders or shift_in.notify_members:
        enrollment_result = await db.execute(
            select(Enrollment).where(
                Enrollment.cohort_id == cohort_id,
                Enrollment.status.in_(_COHORT_TIMELINE_NOTIFY_STATUSES),
            )
        )
        notify_enrollments = enrollment_result.scalars().all()

    if shift_in.reset_start_reminders:
        for enrollment in notify_enrollments:
            existing = list(enrollment.reminders_sent or [])
            filtered = [
                key for key in existing if key not in _START_COUNTDOWN_REMINDER_KEYS
            ]
            if filtered != existing:
                enrollment.reminders_sent = filtered
                reminder_resets_applied += 1

    await db.commit()
    await db.refresh(cohort)

    notification_attempts = 0
    notification_sent = 0
    if shift_in.notify_members and notify_enrollments:
        try:
            member_ids = list(
                {str(e.member_id) for e in notify_enrollments if e.member_id}
            )
            member_map = {
                member["id"]: member
                for member in await get_members_bulk(
                    member_ids, calling_service="academy"
                )
            }
            email_client = get_email_client()

            async def _send_notice(member_payload: dict) -> bool:
                full_name = (
                    f"{member_payload.get('first_name', '')} {member_payload.get('last_name', '')}"
                ).strip() or "Swimmer"
                return await email_client.send(
                    to_email=member_payload["email"],
                    subject=f"Schedule updated: {cohort.name}",
                    body=_build_shift_notice_body(
                        member_name=full_name,
                        cohort_name=cohort.name,
                        old_start=old_start,
                        old_end=old_end,
                        new_start=new_start_utc,
                        new_end=new_end_utc,
                        reason=shift_in.reason,
                    ),
                )

            send_coroutines = []
            for member in member_map.values():
                if member.get("email"):
                    notification_attempts += 1
                    send_coroutines.append(_send_notice(member))

            if send_coroutines:
                send_results = await asyncio.gather(
                    *send_coroutines, return_exceptions=True
                )
                for result in send_results:
                    if result is True:
                        notification_sent += 1
                    elif isinstance(result, Exception):
                        warnings.append(f"Member notification error: {result}")
        except Exception as exc:
            warnings.append(
                "Member notifications skipped due to member lookup/send failure: "
                f"{exc}"
            )

    actor_member_id = None
    try:
        actor_member = await get_member_by_auth_id(
            current_user.user_id, calling_service="academy"
        )
        if actor_member:
            actor_member_id = actor_member.get("id")
    except Exception as exc:
        warnings.append(f"Could not resolve actor member for audit log: {exc}")

    log_row = CohortTimelineShiftLog(
        cohort_id=cohort_id,
        idempotency_key=idempotency_key,
        actor_auth_id=current_user.user_id,
        actor_member_id=actor_member_id,
        reason=shift_in.reason,
        old_start_date=old_start,
        old_end_date=old_end,
        new_start_date=new_start_utc,
        new_end_date=new_end_utc,
        delta_seconds=int(delta.total_seconds()),
        options_json={
            "shift_sessions": bool(shift_in.shift_sessions),
            "shift_installments": bool(shift_in.shift_installments),
            "reset_start_reminders": bool(shift_in.reset_start_reminders),
            "notify_members": bool(shift_in.notify_members),
            "set_status_to_open_if_future": bool(shift_in.set_status_to_open_if_future),
        },
        results_json={
            "already_applied": False,
            "sessions_shifted": sessions_shifted,
            "sessions_skipped": sessions_skipped,
            "pending_installments_shifted": pending_installments_shifted,
            "reminder_resets_applied": reminder_resets_applied,
            "notification_attempts": notification_attempts,
            "notification_sent": notification_sent,
        },
        warnings=warnings,
    )
    db.add(log_row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if idempotency_key:
            existing_log_result = await db.execute(
                select(CohortTimelineShiftLog).where(
                    CohortTimelineShiftLog.cohort_id == cohort_id,
                    CohortTimelineShiftLog.idempotency_key == idempotency_key,
                )
            )
            existing_log = existing_log_result.scalar_one_or_none()
            if existing_log:
                return _timeline_shift_response_from_log(existing_log)
        warnings.append("Audit log write failed due to idempotency conflict")

    return CohortTimelineShiftApplyResponse(
        cohort_id=cohort_id,
        old_start_date=old_start,
        old_end_date=old_end,
        new_start_date=new_start_utc,
        new_end_date=new_end_utc,
        delta_seconds=int(delta.total_seconds()),
        already_applied=False,
        sessions_shifted=sessions_shifted,
        sessions_skipped=sessions_skipped,
        pending_installments_shifted=pending_installments_shifted,
        reminder_resets_applied=reminder_resets_applied,
        notification_attempts=notification_attempts,
        notification_sent=notification_sent,
        warnings=warnings,
    )


@router.get(
    "/cohorts/{cohort_id}/timeline-shifts",
    response_model=List[CohortTimelineShiftLogResponse],
)
async def list_cohort_timeline_shift_logs(
    cohort_id: uuid.UUID,
    limit: int = 20,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List immutable timeline-shift audit logs for a cohort (newest first)."""
    capped_limit = max(1, min(limit, 100))
    result = await db.execute(
        select(CohortTimelineShiftLog)
        .where(CohortTimelineShiftLog.cohort_id == cohort_id)
        .order_by(CohortTimelineShiftLog.created_at.desc())
        .limit(capped_limit)
    )
    return result.scalars().all()


@router.get(
    "/cohorts/{cohort_id}/resources", response_model=List[CohortResourceResponse]
)
async def list_cohort_resources(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """List resources for a specific cohort. Accessible by coach or admin."""
    await require_coach_for_cohort(current_user, str(cohort_id), db)

    query = (
        select(CohortResource)
        .where(CohortResource.cohort_id == cohort_id)
        .order_by(
            CohortResource.week_number.asc().nullsfirst(),
            CohortResource.created_at.asc(),
        )
    )
    result = await db.execute(query)
    return result.scalars().all()
