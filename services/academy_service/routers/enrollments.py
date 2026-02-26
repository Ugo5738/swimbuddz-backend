import httpx
from fastapi import APIRouter
from services.academy_service.routers._shared import (
    AdminDropoutActionRequest,
    AsyncSession,
    AuthUser,
    Cohort,
    CohortStatus,
    Depends,
    Enrollment,
    EnrollmentCreate,
    EnrollmentInstallment,
    EnrollmentMarkPaidRequest,
    EnrollmentResponse,
    EnrollmentStatus,
    EnrollmentUpdate,
    HTTPException,
    InstallmentStatus,
    List,
    Milestone,
    Optional,
    PaymentStatus,
    Program,
    StudentProgress,
    _list_enrollment_installments,
    _resolve_enrollment_total_fee,
    _sync_installment_state_for_enrollment,
    debit_member_wallet,
    func,
    get_async_db,
    get_current_user,
    get_email_client,
    get_logger,
    get_member_by_auth_id,
    get_member_by_id,
    get_settings,
    internal_post,
    kobo_to_bubbles,
    require_admin,
    select,
    selectinload,
    sync_enrollment_installment_state,
    utc_now,
    uuid,
)

router = APIRouter(tags=["academy"])
logger = get_logger(__name__)


# --- Enrollments ---


@router.post("/enrollments", response_model=EnrollmentResponse)
async def enroll_student(
    enrollment_in: EnrollmentCreate,
    current_user: AuthUser = Depends(require_admin),  # Admin can enroll anyone
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Enrollment).where(
        Enrollment.member_id == enrollment_in.member_id,
        Enrollment.program_id == enrollment_in.program_id,
    )
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=400, detail="Member already enrolled in this cohort"
        )

    program = (
        await db.execute(select(Program).where(Program.id == enrollment_in.program_id))
    ).scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    cohort = None
    if enrollment_in.cohort_id:
        cohort = (
            await db.execute(select(Cohort).where(Cohort.id == enrollment_in.cohort_id))
        ).scalar_one_or_none()
        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found")
        if cohort.program_id != enrollment_in.program_id:
            raise HTTPException(
                status_code=400, detail="Cohort does not belong to the selected program"
            )

    price_snapshot = _resolve_enrollment_total_fee(program, cohort)
    enrollment = Enrollment(
        program_id=enrollment_in.program_id,
        cohort_id=enrollment_in.cohort_id,
        member_id=enrollment_in.member_id,
        status=EnrollmentStatus.ENROLLED,  # Admin enrolls directly
        payment_status=PaymentStatus.PENDING,
        uses_installments=False,
        preferences=enrollment_in.preferences,
        price_snapshot_amount=price_snapshot,
        currency_snapshot=program.currency or "NGN",
    )
    db.add(enrollment)

    await db.flush()
    await _sync_installment_state_for_enrollment(db, enrollment)

    await db.commit()

    refreshed = await db.execute(
        select(Enrollment)
        .where(Enrollment.id == enrollment.id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    return refreshed.scalar_one()


@router.get("/enrollments", response_model=List[EnrollmentResponse])
async def list_enrollments(
    status: Optional[EnrollmentStatus] = None,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all enrollments (admin only). Filter by status optional."""

    query = (
        select(Enrollment)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
        .order_by(Enrollment.created_at.desc())
    )

    if status:
        query = query.where(Enrollment.status == status)

    result = await db.execute(query)
    enrollments = result.scalars().all()
    for enrollment in enrollments:
        await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()
    return enrollments


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def get_enrollment(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get detailed enrollment info. Accessible by admins and coaches."""

    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()

    # Enrich with member name/email
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
        pass

    return data


@router.patch("/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def update_enrollment(
    enrollment_id: uuid.UUID,
    enrollment_in: EnrollmentUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update enrollment status and/or payment status."""

    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    update_data = enrollment_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(enrollment, field, value)

    await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()

    # Reload with relationships eager loaded to avoid lazy-load during response serialization
    refreshed = await db.execute(query)
    enrollment = refreshed.scalar_one_or_none()
    return enrollment


@router.post("/enrollments/me", response_model=EnrollmentResponse)
async def self_enroll(
    request_data: dict,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Allow a member to request enrollment in a program or specific cohort.
    If 'program_id' only: Creates a PENDING_APPROVAL request.
    If 'cohort_id': Validates cohort and creates PENDING_APPROVAL request.
    """
    program_id_str = request_data.get("program_id")
    cohort_id_str = request_data.get("cohort_id")
    preferences = request_data.get("preferences")

    if not program_id_str and not cohort_id_str:
        raise HTTPException(
            status_code=422, detail="Either program_id or cohort_id is required"
        )

    program_id = uuid.UUID(program_id_str) if program_id_str else None
    cohort_id = uuid.UUID(cohort_id_str) if cohort_id_str else None
    program = None
    cohort = None

    # 1. Get Member ID via members-service
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member:
        raise HTTPException(
            status_code=404,
            detail="Member profile not found. Please complete registration.",
        )

    # 2. Derive/Validate Program ID
    if cohort_id:
        cohort_query = select(Cohort).where(Cohort.id == cohort_id)
        cohort_result = await db.execute(cohort_query)
        cohort = cohort_result.scalar_one_or_none()

        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found")

        # If user picked a cohort, ensure we set the program_id correctly
        if program_id and program_id != cohort.program_id:
            raise HTTPException(
                status_code=400,
                detail="Cohort does not belong to the specified program",
            )
        program_id = cohort.program_id

        # Check if the cohort's program is published
        program_query = select(Program).where(Program.id == cohort.program_id)
        program_result = await db.execute(program_query)
        program = program_result.scalar_one_or_none()

        if not program or not program.is_published:
            raise HTTPException(
                status_code=400,
                detail="This program is not yet available for enrollment.",
            )

        # Validation for mid-entry if cohort is ACTIVE
        if cohort.status == CohortStatus.ACTIVE:
            # Check if mid-entry is allowed for this cohort
            if not cohort.allow_mid_entry:
                raise HTTPException(
                    status_code=400,
                    detail="This cohort does not allow mid-entry. Please join a waitlist or select another cohort.",
                )

            # Check if within cutoff window
            now = utc_now()
            days_since_start = (now - cohort.start_date).days
            current_week = (days_since_start // 7) + 1

            if current_week > cohort.mid_entry_cutoff_week:
                raise HTTPException(
                    status_code=400,
                    detail=f"Mid-entry window has closed (week {current_week} > cutoff week {cohort.mid_entry_cutoff_week}). Please join a waitlist or select another cohort.",
                )

    elif program_id:
        # User is requesting to join a generic Program (Request-based)
        program_query = select(Program).where(Program.id == program_id)
        program_result = await db.execute(program_query)
        program = program_result.scalar_one_or_none()
        if not program:
            raise HTTPException(status_code=404, detail="Program not found")

        # Check if program is published
        if not program.is_published:
            raise HTTPException(
                status_code=400,
                detail="This program is not yet available for enrollment.",
            )

    # 3. Check Existing Enrollment/Request
    # Only block enrolling in the SAME COHORT twice.
    # Allow:
    #   - Different cohorts in same program (e.g., future cohorts)
    #   - Different programs simultaneously
    if cohort_id:
        query = select(Enrollment).where(
            Enrollment.member_id == member["id"],
            Enrollment.cohort_id == cohort_id,  # Only check same cohort, not program
            Enrollment.status.in_(
                [EnrollmentStatus.ENROLLED, EnrollmentStatus.PENDING_APPROVAL]
            ),
        )
        result = await db.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            detail = (
                "You are already enrolled in this cohort"
                if existing.status == EnrollmentStatus.ENROLLED
                else "You already have a pending request for this cohort"
            )
            raise HTTPException(status_code=400, detail=detail)

    # 4. Check Capacity and Determine Status
    # If cohort is at capacity, set status to WAITLIST instead of PENDING_APPROVAL
    enrollment_status = EnrollmentStatus.PENDING_APPROVAL

    if cohort_id and cohort:
        # Count current enrollments (ENROLLED + PENDING_APPROVAL)

        enrolled_count_result = await db.execute(
            select(func.count(Enrollment.id)).where(
                Enrollment.cohort_id == cohort_id,
                Enrollment.status.in_(
                    [EnrollmentStatus.ENROLLED, EnrollmentStatus.PENDING_APPROVAL]
                ),
            )
        )
        enrolled_count = enrolled_count_result.scalar() or 0

        if enrolled_count >= cohort.capacity:
            # Cohort is at capacity - add to waitlist
            enrollment_status = EnrollmentStatus.WAITLIST

    # 5. Create Enrollment Request
    # Status is PENDING_APPROVAL by default, or WAITLIST if at capacity
    # Payment status handles the financial part separate from Admission.
    enrollment = Enrollment(
        program_id=program_id,
        cohort_id=cohort_id,  # Can be None
        member_id=member["id"],
        member_auth_id=current_user.user_id,  # For decoupled ownership verification
        status=enrollment_status,
        payment_status=PaymentStatus.PENDING,
        uses_installments=False,
        preferences=preferences or {},
        price_snapshot_amount=(
            _resolve_enrollment_total_fee(program, cohort)
            if (program and cohort)
            else None
        ),
        currency_snapshot=(program.currency if program else None),
    )
    db.add(enrollment)

    await db.flush()
    await _sync_installment_state_for_enrollment(db, enrollment)

    await db.commit()

    # Re-fetch with relationships to avoid lazy loading issues
    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment.id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.get("/my-enrollments", response_model=List[EnrollmentResponse])
async def get_my_enrollments(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get all enrollments for the current user."""

    query = (
        select(Enrollment)
        .where(Enrollment.member_auth_id == current_user.user_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    result = await db.execute(query)
    enrollments = result.scalars().all()
    for enrollment in enrollments:
        await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()
    return enrollments


@router.get("/my-enrollments/{enrollment_id}/waitlist-position")
async def get_my_waitlist_position(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get waitlist position for a waitlisted enrollment."""

    # Get the enrollment
    query = select(Enrollment).where(
        Enrollment.id == enrollment_id,
        Enrollment.member_auth_id == current_user.user_id,
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    if enrollment.status != EnrollmentStatus.WAITLIST:
        return {"position": None, "message": "Not on waitlist"}

    if not enrollment.cohort_id:
        return {"position": None, "message": "No cohort assigned"}

    # Count waitlist entries created before this one (position = count + 1)
    position_result = await db.execute(
        select(func.count(Enrollment.id)).where(
            Enrollment.cohort_id == enrollment.cohort_id,
            Enrollment.status == EnrollmentStatus.WAITLIST,
            Enrollment.created_at < enrollment.created_at,
        )
    )
    position = (position_result.scalar() or 0) + 1

    return {
        "enrollment_id": str(enrollment_id),
        "cohort_id": str(enrollment.cohort_id),
        "position": position,
        "status": enrollment.status.value,
    }


@router.get("/my-enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def get_my_enrollment(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a specific enrollment for the current member."""

    query = (
        select(Enrollment)
        .where(
            Enrollment.id == enrollment_id,
            Enrollment.member_auth_id == current_user.user_id,
        )
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()
    return enrollment


@router.post(
    "/admin/enrollments/{enrollment_id}/mark-paid", response_model=EnrollmentResponse
)
async def admin_mark_enrollment_paid(
    enrollment_id: uuid.UUID,
    payload: EnrollmentMarkPaidRequest | None = None,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Mark an enrollment as paid (service-to-service call from payments_service).
    Updates payment_status to PAID and enrollment status to ENROLLED if pending.
    """
    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    if payload and payload.paid_at:
        now_dt = payload.paid_at
    else:
        now_dt = utc_now()
    mark_payload = payload or EnrollmentMarkPaidRequest()

    installments = await _sync_installment_state_for_enrollment(
        db, enrollment, now_dt=now_dt
    )
    was_any_installment_paid = enrollment.paid_installments_count > 0

    if installments:
        paid_statuses = {InstallmentStatus.PAID, InstallmentStatus.WAIVED}
        target_installment: EnrollmentInstallment | None = None

        if mark_payload.clear_installments:
            for inst in installments:
                await db.delete(inst)
            enrollment.uses_installments = False
            enrollment.total_installments = 0
            enrollment.paid_installments_count = 0
            enrollment.missed_installments_count = 0
            enrollment.access_suspended = False
            enrollment.payment_status = PaymentStatus.PAID
            enrollment.paid_at = now_dt
            if mark_payload.payment_reference:
                enrollment.payment_reference = mark_payload.payment_reference
            if enrollment.status == EnrollmentStatus.PENDING_APPROVAL:
                cohort = enrollment.cohort
                if not cohort or not cohort.require_approval:
                    enrollment.status = EnrollmentStatus.ENROLLED
            installments = []
        elif mark_payload.installment_id:
            target_installment = next(
                (i for i in installments if i.id == mark_payload.installment_id), None
            )
            if not target_installment:
                raise HTTPException(status_code=400, detail="installment_id is invalid")
        elif mark_payload.installment_number:
            target_installment = next(
                (
                    i
                    for i in installments
                    if i.installment_number == mark_payload.installment_number
                ),
                None,
            )
            if not target_installment:
                raise HTTPException(
                    status_code=400, detail="installment_number is invalid"
                )
        else:
            target_installment = next(
                (i for i in installments if i.status not in paid_statuses),
                None,
            )

        if not mark_payload.clear_installments:
            if target_installment and target_installment.status not in paid_statuses:
                target_installment.status = InstallmentStatus.PAID
                target_installment.paid_at = now_dt
                target_installment.payment_reference = mark_payload.payment_reference

            if mark_payload.payment_reference:
                enrollment.payment_reference = mark_payload.payment_reference

            await _sync_installment_state_for_enrollment(db, enrollment, now_dt=now_dt)
    else:
        enrollment.payment_status = PaymentStatus.PAID
        enrollment.payment_reference = mark_payload.payment_reference
        enrollment.paid_at = now_dt
        if enrollment.status == EnrollmentStatus.PENDING_APPROVAL:
            cohort = enrollment.cohort
            if not cohort or not cohort.require_approval:
                enrollment.status = EnrollmentStatus.ENROLLED

    await db.commit()

    should_send_confirmation = (
        not was_any_installment_paid and enrollment.paid_installments_count > 0
    ) or (not installments and enrollment.payment_status == PaymentStatus.PAID)

    # Send enrollment confirmation email on first successful installment.
    if should_send_confirmation:
        try:
            member_data = await get_member_by_id(
                str(enrollment.member_id), calling_service="academy"
            )
            if member_data:
                member_email = member_data.get("email")
                member_name = member_data.get("first_name", "Member")

                if member_email and enrollment.cohort:
                    program_name = (
                        enrollment.program.name
                        if enrollment.program
                        else "Academy Program"
                    )
                    cohort_name = enrollment.cohort.name
                    start_date = (
                        enrollment.cohort.start_date.strftime("%B %d, %Y")
                        if enrollment.cohort.start_date
                        else "TBD"
                    )
                    location = enrollment.cohort.location_name or None

                    # Resolve coach name from assignments if available
                    coach_name = None
                    if (
                        hasattr(enrollment.cohort, "coach_assignments")
                        and enrollment.cohort.coach_assignments
                    ):
                        lead = (
                            next(
                                (
                                    a
                                    for a in enrollment.cohort.coach_assignments
                                    if getattr(a, "role", None) == "lead"
                                ),
                                None,
                            )
                            or enrollment.cohort.coach_assignments[0]
                        )
                        if lead and hasattr(lead, "coach") and lead.coach:
                            coach_name = f"{lead.coach.first_name} {lead.coach.last_name}".strip()

                    # Build installment schedule lines if paying in installments
                    is_installment = bool(installments)
                    installment_schedule = None
                    if is_installment and installments:
                        installment_schedule = [
                            f"Installment {inst.installment_number}: "
                            f"₦{round(inst.amount / 100):,} due "
                            f"{inst.due_at.strftime('%B %d, %Y') if hasattr(inst.due_at, 'strftime') else str(inst.due_at)}"
                            for inst in sorted(
                                installments, key=lambda i: i.installment_number
                            )
                        ]

                    email_client = get_email_client()
                    await email_client.send_template(
                        template_type="enrollment_confirmation",
                        to_email=member_email,
                        template_data={
                            "member_name": member_name,
                            "program_name": program_name,
                            "cohort_name": cohort_name,
                            "start_date": start_date,
                            "location": location,
                            "coach_name": coach_name,
                            "is_installment": is_installment,
                            "installment_schedule": installment_schedule,
                        },
                    )
        except Exception as e:
            logger.warning(f"Failed to send enrollment confirmation email: {e}")

    # Activate the academy tier on the member for the duration of this cohort.
    # We do this on every installment payment (not just the first) so that if a
    # later cohort ends after the current academy_paid_until, the date is extended.
    # The members_service endpoint keeps whichever date is later, so it is safe to
    # call multiple times.
    try:
        _settings = get_settings()
        cohort_end = enrollment.cohort.end_date if enrollment.cohort else None
        member_auth_id = None
        if enrollment.member_id:
            member_data = await get_member_by_id(
                str(enrollment.member_id), calling_service="academy"
            )
            if member_data:
                member_auth_id = member_data.get("auth_id")

        if cohort_end and member_auth_id:
            end_iso = (
                cohort_end.isoformat()
                if hasattr(cohort_end, "isoformat")
                else str(cohort_end)
            )
            await internal_post(
                service_url=_settings.MEMBERS_SERVICE_URL,
                path=f"/admin/members/by-auth/{member_auth_id}/academy/activate",
                calling_service="academy",
                json={"cohort_end_date": end_iso},
            )
        else:
            logger.warning(
                f"Skipping academy tier activation for enrollment {enrollment_id}: "
                f"cohort_end={cohort_end}, member_auth_id={member_auth_id}"
            )
    except Exception as e:
        # Non-fatal — enrollment payment succeeded; log and continue
        logger.error(
            f"Failed to activate academy tier for enrollment {enrollment_id}: {e}"
        )

    # Re-fetch with relationships for response
    result = await db.execute(query)
    return result.scalar_one()


@router.post(
    "/admin/enrollments/{enrollment_id}/dropout-action",
    response_model=EnrollmentResponse,
)
async def admin_dropout_action(
    enrollment_id: uuid.UUID,
    payload: AdminDropoutActionRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin action on a DROPOUT_PENDING enrollment.

    action="approve" → confirms the dropout, moves enrollment to DROPPED.
    action="reverse"  → reinstates the student, moves enrollment back to ENROLLED
                        (or PENDING_APPROVAL if the cohort requires it).
                        The missed_installments_count is NOT reset — it is a
                        permanent behavioral counter.
    """
    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    if enrollment.status != EnrollmentStatus.DROPOUT_PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Enrollment is not in dropout_pending state (current: {enrollment.status})",
        )

    if payload.action == "approve":
        enrollment.status = EnrollmentStatus.DROPPED
        enrollment.access_suspended = True
        enrollment.payment_status = PaymentStatus.FAILED
        logger.info(
            f"Admin {current_user.id} approved dropout for enrollment {enrollment_id}"
        )

    elif payload.action == "reverse":
        # Reinstate the student. Access is restored only if installments are current.
        # missed_installments_count stays as-is (permanent behavioral record).
        cohort = enrollment.cohort
        requires_approval = bool(cohort.require_approval) if cohort else False
        enrollment.status = (
            EnrollmentStatus.PENDING_APPROVAL
            if requires_approval
            else EnrollmentStatus.ENROLLED
        )
        enrollment.access_suspended = False
        enrollment.payment_status = PaymentStatus.PAID

        # Re-sync to correctly set access_suspended based on actual installment state
        if cohort:
            program = enrollment.program or (cohort.program if cohort else None)
            if program:
                installments = list(enrollment.installments or [])
                sync_enrollment_installment_state(
                    enrollment=enrollment,
                    installments=installments,
                    duration_weeks=int(program.duration_weeks),
                    cohort_start=cohort.start_date,
                    cohort_requires_approval=requires_approval,
                    admin_dropout_approval=bool(cohort.admin_dropout_approval),
                    now=utc_now(),
                )
                # Override: admin has manually reinstated, so force out of dropout states
                if enrollment.status in (
                    EnrollmentStatus.DROPPED,
                    EnrollmentStatus.DROPOUT_PENDING,
                ):
                    enrollment.status = (
                        EnrollmentStatus.PENDING_APPROVAL
                        if requires_approval
                        else EnrollmentStatus.ENROLLED
                    )

        logger.info(
            f"Admin {current_user.id} reversed dropout for enrollment {enrollment_id}"
        )

    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid action. Must be 'approve' or 'reverse'.",
        )

    await db.commit()

    result = await db.execute(query)
    return result.scalar_one()


@router.get("/cohorts/{cohort_id}/enrollments", response_model=List[EnrollmentResponse])
async def list_cohort_enrollments(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = (
        select(Enrollment)
        .where(Enrollment.cohort_id == cohort_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    result = await db.execute(query)
    enrollments = result.scalars().all()
    for enrollment in enrollments:
        await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()
    return enrollments


@router.get("/cohorts/{cohort_id}/analytics")
async def get_cohort_analytics(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get detailed analytics for a cohort including:
    - Total students, completion rates, at-risk students, avg scores
    """
    # Get cohort
    cohort_query = (
        select(Cohort)
        .options(selectinload(Cohort.program))
        .where(Cohort.id == cohort_id)
    )
    cohort_result = await db.execute(cohort_query)
    cohort = cohort_result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    # Get enrolled students count
    enrolled_query = select(func.count(Enrollment.id)).where(
        Enrollment.cohort_id == cohort_id,
        Enrollment.status == EnrollmentStatus.ENROLLED,
    )
    enrolled_result = await db.execute(enrolled_query)
    total_students = enrolled_result.scalar() or 0

    # Get all milestones for the program
    program_id = cohort.program_id
    milestone_query = select(Milestone).where(Milestone.program_id == program_id)
    milestone_result = await db.execute(milestone_query)
    all_milestones = milestone_result.scalars().all()
    total_milestones = len(all_milestones)

    # Get all progress records for this cohort's enrollments
    progress_query = (
        select(StudentProgress)
        .join(Enrollment, StudentProgress.enrollment_id == Enrollment.id)
        .where(Enrollment.cohort_id == cohort_id)
    )
    progress_result = await db.execute(progress_query)
    all_progress = progress_result.scalars().all()

    # Calculate stats
    achieved_count = len([p for p in all_progress if p.status.value == "achieved"])
    pending_count = len([p for p in all_progress if p.status.value == "pending"])
    in_review_count = len([p for p in all_progress if p.status.value == "in_review"])

    # Completion rate (achieved / (total_students * total_milestones))
    possible_total = total_students * total_milestones
    completion_rate = (
        round((achieved_count / possible_total) * 100) if possible_total > 0 else 0
    )

    # Average score (only for achieved with scores)
    scored = [p for p in all_progress if p.score is not None]
    avg_score = round(sum(p.score for p in scored) / len(scored)) if scored else None

    # At-risk students (0 progress in last 14 days)
    from datetime import timedelta

    fourteen_days_ago = utc_now() - timedelta(days=14)

    # Get enrollments with no recent activity
    enrollment_ids_query = select(Enrollment.id).where(
        Enrollment.cohort_id == cohort_id,
        Enrollment.status == EnrollmentStatus.ENROLLED,
    )
    enrollment_result = await db.execute(enrollment_ids_query)
    all_enrollment_ids = set(row[0] for row in enrollment_result.fetchall())

    active_enrollment_ids = set(
        p.enrollment_id
        for p in all_progress
        if p.updated_at and p.updated_at >= fourteen_days_ago
    )
    at_risk_count = len(all_enrollment_ids - active_enrollment_ids)

    return {
        "cohort_id": str(cohort_id),
        "cohort_name": cohort.name,
        "program_name": cohort.program.name if cohort.program else None,
        "total_students": total_students,
        "total_milestones": total_milestones,
        "milestones_achieved": achieved_count,
        "milestones_pending": pending_count,
        "milestones_in_review": in_review_count,
        "completion_rate": completion_rate,
        "avg_score": avg_score,
        "students_at_risk": at_risk_count,
    }


@router.post(
    "/enrollments/{enrollment_id}/installments/{installment_id}/pay-with-bubbles",
    response_model=EnrollmentResponse,
)
async def pay_installment_with_bubbles(
    enrollment_id: uuid.UUID,
    installment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Pay a pending enrollment installment using Bubbles wallet.

    - Deducts the installment amount from the member's wallet.
    - Marks the installment as PAID with the wallet transaction ID.
    - Re-syncs enrollment payment status (may lift suspension, unlock access).
    - Idempotent: repeated calls return the current state without double-charging.
    """

    # Load enrollment (member must own it)
    member_data = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member_data:
        raise HTTPException(status_code=404, detail="Member profile not found")

    result = await db.execute(
        select(Enrollment)
        .where(
            Enrollment.id == enrollment_id,
            Enrollment.member_id == member_data["id"],
        )
        .options(selectinload(Enrollment.program))
    )
    enrollment = result.scalar_one_or_none()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    # Load the specific installment
    inst_result = await db.execute(
        select(EnrollmentInstallment).where(
            EnrollmentInstallment.id == installment_id,
            EnrollmentInstallment.enrollment_id == enrollment_id,
        )
    )
    installment = inst_result.scalar_one_or_none()
    if not installment:
        raise HTTPException(status_code=404, detail="Installment not found")

    # Idempotency: already paid
    paid_statuses = {InstallmentStatus.PAID, InstallmentStatus.WAIVED}
    if installment.status in paid_statuses:
        # Re-sync and return current state
        all_installments = await _list_enrollment_installments(db, enrollment_id)
        cohort = await db.get(Cohort, enrollment.cohort_id)
        if cohort:
            sync_enrollment_installment_state(
                enrollment=enrollment,
                installments=all_installments,
                duration_weeks=cohort.duration_weeks,
                cohort_start=cohort.start_date,
                cohort_requires_approval=cohort.require_approval,
            )
        await db.commit()
        await db.refresh(enrollment)
        return EnrollmentResponse.model_validate(enrollment)

    fee_bubbles = kobo_to_bubbles(installment.amount)
    if fee_bubbles <= 0:
        raise HTTPException(
            status_code=400,
            detail="Installment amount is too small to pay with Bubbles",
        )

    idempotency_key = f"installment-{installment.id}"
    try:
        txn_result = await debit_member_wallet(
            current_user.user_id,
            amount=fee_bubbles,
            idempotency_key=idempotency_key,
            description=f"Academy installment #{installment.installment_number} ({fee_bubbles} 🫧)",
            calling_service="academy",
            transaction_type="purchase",
            reference_type="enrollment",
            reference_id=str(enrollment_id),
        )
        wallet_txn_id = txn_result.get("transaction_id")
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
        raise HTTPException(
            status_code=502, detail="Payment service error. Please try again."
        )

    # Mark installment as paid
    installment.status = InstallmentStatus.PAID
    installment.paid_at = utc_now()
    installment.payment_reference = str(wallet_txn_id) if wallet_txn_id else "bubbles"

    # Re-sync enrollment state
    all_installments = await _list_enrollment_installments(db, enrollment_id)
    cohort = await db.get(Cohort, enrollment.cohort_id)
    if cohort:
        sync_enrollment_installment_state(
            enrollment=enrollment,
            installments=all_installments,
            duration_weeks=cohort.duration_weeks,
            cohort_start=cohort.start_date,
            cohort_requires_approval=cohort.require_approval,
        )

    await db.commit()
    await db.refresh(enrollment)
    return EnrollmentResponse.model_validate(enrollment)
