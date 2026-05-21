from fastapi import APIRouter

from libs.common.service_client import emit_rewards_event
from services.academy_service.routers._shared import (
    AsyncSession,
    AuthUser,
    Cohort,
    Depends,
    Enrollment,
    EnrollmentStatus,
    HTTPException,
    List,
    MemberMilestoneClaimRequest,
    Milestone,
    MilestoneEventType,
    MilestoneReviewEvent,
    MilestoneReviewEventResponse,
    OverrideProgressRequest,
    ProgressStatus,
    RequiredEvidence,
    StudentProgress,
    StudentProgressResponse,
    StudentProgressUpdate,
    _sync_installment_state_for_enrollment,
    get_async_db,
    get_current_user,
    get_logger,
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


# --- Progress ---


@router.post("/progress", response_model=StudentProgressResponse)
async def update_student_progress(
    progress_in: StudentProgressUpdate,
    enrollment_id: uuid.UUID,
    milestone_id: uuid.UUID,
    current_user: AuthUser = Depends(require_coach),  # Coach or Admin
    db: AsyncSession = Depends(get_async_db),
):
    """Update or create student progress (Coach/Admin only).

    Accessible by:
    - Admins (can update any enrollment)
    - Coaches (can only update enrollments in their assigned cohorts)
    """
    # First, get the enrollment to check cohort access
    enrollment_query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    enrollment_result = await db.execute(enrollment_query)
    enrollment = enrollment_result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enrollment not found",
        )

    # Ensure milestone exists for reward metadata and program consistency.
    milestone_query = select(Milestone).where(Milestone.id == milestone_id)
    milestone_result = await db.execute(milestone_query)
    milestone = milestone_result.scalar_one_or_none()
    if not milestone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Milestone not found",
        )
    if milestone.program_id != enrollment.program_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Milestone does not belong to enrollment program",
        )

    # Verify coach has access to this cohort
    if enrollment.cohort_id:
        await require_coach_for_cohort(current_user, str(enrollment.cohort_id), db)
    else:
        # If no cohort assigned, only admins can update
        from libs.auth.dependencies import is_admin_or_service

        if not is_admin_or_service(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins can update progress for enrollments without a cohort",
            )

    # Check if record exists
    query = select(StudentProgress).where(
        StudentProgress.enrollment_id == enrollment_id,
        StudentProgress.milestone_id == milestone_id,
    )
    result = await db.execute(query)
    progress = result.scalar_one_or_none()

    if progress:
        previous_status = progress.status
        progress.status = progress_in.status
        progress.achieved_at = progress_in.achieved_at
        progress.coach_notes = progress_in.coach_notes
        # ── Freeze invariant ────────────────────────────────────────────
        # ``reviewed_by_coach_id`` is the **original** reviewer's
        # identity — set on first review and never overwritten by a
        # subsequent action through this endpoint. Admin overrides go
        # through ``POST /admin/progress/override``, which records a
        # separate OVERRIDE event and deliberately does not touch this
        # field. See ACADEMY_ADMIN_CONTROLS_DESIGN §5.4 for the
        # rationale; older behaviour silently overwrote and erased the
        # coach from the live row.
        if progress.reviewed_by_coach_id is None:
            if progress_in.reviewed_by_coach_id:
                progress.reviewed_by_coach_id = progress_in.reviewed_by_coach_id
                progress.reviewed_at = progress_in.reviewed_at or utc_now()
            elif progress_in.reviewed_at or progress_in.coach_notes:
                # Auto-fill on first review when coach adds notes or
                # explicit review timestamp.
                progress.reviewed_by_coach_id = current_user.user_id
                progress.reviewed_at = progress_in.reviewed_at or utc_now()
    else:
        previous_status = None
        progress = StudentProgress(
            enrollment_id=enrollment_id,
            milestone_id=milestone_id,
            status=progress_in.status,
            achieved_at=progress_in.achieved_at,
            coach_notes=progress_in.coach_notes,
            reviewed_by_coach_id=progress_in.reviewed_by_coach_id
            or current_user.user_id,
            reviewed_at=progress_in.reviewed_at or utc_now(),
        )
        db.add(progress)

    # Flush so that `progress.id` is populated for the audit event FK
    await db.flush()

    # Classify the event: approval, rejection, or generic status change
    from libs.auth.dependencies import is_admin_or_service

    actor_role = "admin" if is_admin_or_service(current_user) else "coach"
    has_review_payload = bool(
        progress_in.coach_notes
        or progress_in.reviewed_at
        or progress_in.reviewed_by_coach_id
    )
    if progress_in.status == ProgressStatus.ACHIEVED and has_review_payload:
        event_type = MilestoneEventType.APPROVED
    elif progress_in.status == ProgressStatus.PENDING and progress_in.coach_notes:
        event_type = MilestoneEventType.REJECTED
    else:
        event_type = MilestoneEventType.STATUS_CHANGED

    db.add(
        MilestoneReviewEvent(
            progress_id=progress.id,
            enrollment_id=enrollment_id,
            milestone_id=milestone_id,
            event_type=event_type,
            actor_id=current_user.user_id,
            actor_role=actor_role,
            previous_status=previous_status,
            new_status=progress_in.status,
            coach_notes_snapshot=progress_in.coach_notes,
        )
    )

    await db.commit()
    await db.refresh(progress)

    # Best-effort: emit academy milestone reward event
    await emit_rewards_event(
        event_type="academy.milestone_passed",
        member_auth_id=current_user.user_id,
        service_source="academy",
        event_data={
            "milestone_name": milestone.name,
            "enrollment_id": str(enrollment_id),
            "milestone_id": str(milestone_id),
        },
        idempotency_key=f"academy-milestone-{enrollment_id}-{milestone_id}",
        calling_service="academy",
    )

    return progress


@router.get(
    "/enrollments/{enrollment_id}/progress",
    response_model=List[StudentProgressResponse],
)
async def get_student_progress(
    enrollment_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    query = select(StudentProgress).where(
        StudentProgress.enrollment_id == enrollment_id
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/enrollments/{enrollment_id}/progress/{milestone_id}/claim",
    response_model=StudentProgressResponse,
)
async def claim_milestone(
    enrollment_id: uuid.UUID,
    milestone_id: uuid.UUID,
    claim_in: MemberMilestoneClaimRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Allow a member to claim completion of a milestone.

    The member must own the enrollment. Creates or updates a StudentProgress
    record with ACHIEVED status and optional evidence.
    """

    # Verify enrollment exists and belongs to the current user
    enrollment_query = select(Enrollment).where(Enrollment.id == enrollment_id)
    enrollment_result = await db.execute(enrollment_query)
    enrollment = enrollment_result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enrollment not found",
        )

    # Verify ownership using member_auth_id (decoupled architecture)
    if enrollment.member_auth_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only claim milestones for your own enrollments",
        )

    await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()

    # Check enrollment is approved and in good standing before allowing claims
    if enrollment.status == EnrollmentStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your enrollment is awaiting admin approval. You cannot claim milestones yet.",
        )

    if enrollment.status == EnrollmentStatus.DROPPED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your enrollment has been dropped. Contact admin for reactivation.",
        )

    if enrollment.access_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access is suspended until required installment payment is completed.",
        )

    # Verify milestone exists and belongs to the program
    milestone_query = select(Milestone).where(Milestone.id == milestone_id)
    milestone_result = await db.execute(milestone_query)
    milestone = milestone_result.scalar_one_or_none()

    if not milestone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Milestone not found",
        )

    # Validate required evidence
    if (
        milestone.required_evidence
        in (RequiredEvidence.VIDEO, RequiredEvidence.TIME_TRIAL)
        and not claim_in.evidence_media_id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This milestone requires {milestone.required_evidence.value} evidence. Please upload your evidence before submitting.",
        )

    # Get or create progress record
    progress_query = select(StudentProgress).where(
        StudentProgress.enrollment_id == enrollment_id,
        StudentProgress.milestone_id == milestone_id,
    )
    progress_result = await db.execute(progress_query)
    progress = progress_result.scalar_one_or_none()

    if progress:
        # Update existing record (handles both first claim and resubmission after rejection)
        previous_status = progress.status
        progress.status = ProgressStatus.ACHIEVED
        progress.achieved_at = utc_now()
        if claim_in.evidence_media_id:
            progress.evidence_media_id = claim_in.evidence_media_id
        if claim_in.student_notes is not None:
            progress.student_notes = claim_in.student_notes

        # Clear previous review fields so it goes back to "pending review" state.
        # The prior rejection feedback is preserved in the MilestoneReviewEvent
        # audit trail emitted at the time of the rejection.
        progress.reviewed_at = None
        progress.reviewed_by_coach_id = None
        progress.score = None
        progress.coach_notes = None
    else:
        # Create new record
        previous_status = None
        progress = StudentProgress(
            enrollment_id=enrollment_id,
            milestone_id=milestone_id,
            status=ProgressStatus.ACHIEVED,
            achieved_at=utc_now(),
            evidence_media_id=claim_in.evidence_media_id,
            student_notes=claim_in.student_notes,
        )
        db.add(progress)

    # Flush so progress.id is available for the audit FK
    await db.flush()

    db.add(
        MilestoneReviewEvent(
            progress_id=progress.id,
            enrollment_id=enrollment_id,
            milestone_id=milestone_id,
            event_type=MilestoneEventType.CLAIMED,
            actor_id=current_user.user_id,
            actor_role="student",
            previous_status=previous_status,
            new_status=ProgressStatus.ACHIEVED,
            student_notes_snapshot=claim_in.student_notes,
            evidence_media_id_snapshot=claim_in.evidence_media_id,
        )
    )

    await db.commit()
    await db.refresh(progress)
    return progress


@router.get(
    "/enrollments/{enrollment_id}/progress/{progress_id}/events",
    response_model=List[MilestoneReviewEventResponse],
)
async def list_milestone_review_events(
    enrollment_id: uuid.UUID,
    progress_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List the audit trail of claim / review / status-change events for a
    given StudentProgress record.

    Visible to: the enrolled student, the coach assigned to the cohort, or an
    admin.
    """
    # Fetch progress + enrollment to check authorization
    progress_query = select(StudentProgress).where(
        StudentProgress.id == progress_id,
        StudentProgress.enrollment_id == enrollment_id,
    )
    progress_result = await db.execute(progress_query)
    progress = progress_result.scalar_one_or_none()
    if progress is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Progress record not found",
        )

    enrollment_query = select(Enrollment).where(Enrollment.id == enrollment_id)
    enrollment_result = await db.execute(enrollment_query)
    enrollment = enrollment_result.scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enrollment not found",
        )

    from libs.auth.dependencies import is_admin_or_service

    is_owner_student = enrollment.member_auth_id == current_user.user_id
    if not is_owner_student and not is_admin_or_service(current_user):
        # Must be a coach assigned to this cohort
        if enrollment.cohort_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this progress history",
            )
        await require_coach_for_cohort(current_user, str(enrollment.cohort_id), db)

    events_query = (
        select(MilestoneReviewEvent)
        .where(MilestoneReviewEvent.progress_id == progress_id)
        .order_by(MilestoneReviewEvent.created_at.asc())
    )
    events_result = await db.execute(events_query)
    return events_result.scalars().all()


# --- Admin override ---


@router.post(
    "/admin/progress/override",
    response_model=StudentProgressResponse,
    status_code=status.HTTP_200_OK,
)
async def override_progress(
    payload: OverrideProgressRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> StudentProgress:
    """Admin (or AI service) override of a prior decision on a milestone claim.

    Unlike ``POST /progress`` (the coach path), this endpoint:

    * **Requires** ``override_reason`` (enforced by the schema).
    * Records a separate ``OVERRIDE`` event on
      ``milestone_review_events`` chained to the most recent prior
      decision via ``override_of_event_id``. Override-of-override is
      permitted — the chain just deepens.
    * Does **not** touch ``StudentProgress.reviewed_by_coach_id`` — the
      original coach stays attributed to the live row (see
      ACADEMY_ADMIN_CONTROLS_DESIGN §5.4).
    * Attributes the event to the synthetic AI principal when the
      caller authenticated via ``service_role``; otherwise to the
      admin user. ``ai_metadata`` is recorded on the event row
      regardless of caller — it's attribution data that travels with
      the event, not a control flag.

    Returns the (possibly newly-created) ``StudentProgress`` row.
    """
    # Service-role JWTs are attributed to the AI principal; everything
    # else is treated as a human admin. The synthetic principal UUID
    # lives in libs.common.principals so it can be referenced from
    # other services later.
    from libs.common.principals import AI_SERVICE_PRINCIPAL_ID

    is_ai_principal = current_user.role == "service_role"
    actor_id = AI_SERVICE_PRINCIPAL_ID if is_ai_principal else current_user.user_id
    actor_role = "ai_service" if is_ai_principal else "admin"

    # Load enrollment + milestone for sanity checks. We don't gate by
    # cohort here (require_admin already covered authorisation), but
    # we do want the same 404/400 shape as the coach path.
    enrollment = (
        await db.execute(
            select(Enrollment).where(Enrollment.id == payload.enrollment_id)
        )
    ).scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Enrollment not found"
        )

    milestone = (
        await db.execute(select(Milestone).where(Milestone.id == payload.milestone_id))
    ).scalar_one_or_none()
    if milestone is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Milestone not found"
        )
    if milestone.program_id != enrollment.program_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Milestone does not belong to enrollment program",
        )

    # Load (or lazily create) the StudentProgress row. Overrides should
    # normally target an existing claim, but allow create-on-override
    # for the rare case where an admin records a decision before the
    # student claimed it (e.g., backfilling historical data).
    progress = (
        await db.execute(
            select(StudentProgress).where(
                StudentProgress.enrollment_id == payload.enrollment_id,
                StudentProgress.milestone_id == payload.milestone_id,
            )
        )
    ).scalar_one_or_none()

    if progress is None:
        progress = StudentProgress(
            enrollment_id=payload.enrollment_id,
            milestone_id=payload.milestone_id,
            status=payload.new_status,
        )
        db.add(progress)
        previous_status = None
    else:
        previous_status = progress.status
        progress.status = payload.new_status

    # Live-row mutations the override is allowed to make. Crucially,
    # ``reviewed_by_coach_id`` and ``reviewed_at`` are NOT touched.
    if payload.new_status == ProgressStatus.ACHIEVED and progress.achieved_at is None:
        progress.achieved_at = utc_now()
    if payload.new_status == ProgressStatus.PENDING:
        # Re-opening — clear the achievement timestamp so the row
        # reads cleanly as not-yet-achieved.
        progress.achieved_at = None
    if payload.coach_notes is not None:
        progress.coach_notes = payload.coach_notes or None
    if payload.score is not None:
        progress.score = payload.score

    # Flush so progress.id is available for the FK on the event row.
    await db.flush()

    # Find the most recent prior decision event to chain off. We treat
    # APPROVED / REJECTED / OVERRIDE as "decisions"; STATUS_CHANGED and
    # CLAIMED are not decisions for chain purposes. NULL means this is
    # the first decision on the claim — schema allows it.
    prior_event = (
        await db.execute(
            select(MilestoneReviewEvent)
            .where(
                MilestoneReviewEvent.progress_id == progress.id,
                MilestoneReviewEvent.event_type.in_(
                    [
                        MilestoneEventType.APPROVED,
                        MilestoneEventType.REJECTED,
                        MilestoneEventType.OVERRIDE,
                    ]
                ),
            )
            .order_by(MilestoneReviewEvent.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    db.add(
        MilestoneReviewEvent(
            progress_id=progress.id,
            enrollment_id=payload.enrollment_id,
            milestone_id=payload.milestone_id,
            event_type=MilestoneEventType.OVERRIDE,
            actor_id=actor_id,
            actor_role=actor_role,
            previous_status=previous_status,
            new_status=payload.new_status,
            coach_notes_snapshot=progress.coach_notes,
            evidence_media_id_snapshot=progress.evidence_media_id,
            score_snapshot=progress.score,
            override_of_event_id=prior_event.id if prior_event else None,
            override_reason=payload.override_reason,
            ai_metadata=payload.ai_metadata,
        )
    )

    await db.commit()
    await db.refresh(progress)

    logger.info(
        "Override recorded: progress=%s actor=%s:%s new_status=%s prior_event=%s",
        progress.id,
        actor_role,
        actor_id,
        payload.new_status,
        prior_event.id if prior_event else "none",
    )

    return progress
