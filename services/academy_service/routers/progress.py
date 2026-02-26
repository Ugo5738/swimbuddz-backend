from fastapi import APIRouter
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
    ProgressStatus,
    StudentProgress,
    StudentProgressResponse,
    StudentProgressUpdate,
    _sync_installment_state_for_enrollment,
    get_async_db,
    get_current_user,
    get_logger,
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
        progress.status = progress_in.status
        progress.achieved_at = progress_in.achieved_at
        progress.coach_notes = progress_in.coach_notes
        # Set review fields if provided or auto-fill for verification
        if progress_in.reviewed_by_coach_id:
            progress.reviewed_by_coach_id = progress_in.reviewed_by_coach_id
        elif progress_in.reviewed_at or progress_in.coach_notes:
            # Auto-fill review info when coach adds notes or review timestamp
            progress.reviewed_by_coach_id = current_user.user_id
            progress.reviewed_at = progress_in.reviewed_at or utc_now()
    else:
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

    await db.commit()
    await db.refresh(progress)
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

    # Get or create progress record
    progress_query = select(StudentProgress).where(
        StudentProgress.enrollment_id == enrollment_id,
        StudentProgress.milestone_id == milestone_id,
    )
    progress_result = await db.execute(progress_query)
    progress = progress_result.scalar_one_or_none()

    if progress:
        # Update existing record (handles both first claim and resubmission after rejection)
        progress.status = ProgressStatus.ACHIEVED
        progress.achieved_at = utc_now()
        if claim_in.evidence_media_id:
            progress.evidence_media_id = claim_in.evidence_media_id
        if claim_in.student_notes is not None:
            progress.student_notes = claim_in.student_notes

        # Clear previous review fields so it goes back to "pending review" state
        progress.reviewed_at = None
        progress.reviewed_by_coach_id = None
        progress.score = None
        progress.coach_notes = None
    else:
        # Create new record
        progress = StudentProgress(
            enrollment_id=enrollment_id,
            milestone_id=milestone_id,
            status=ProgressStatus.ACHIEVED,
            achieved_at=utc_now(),
            evidence_media_id=claim_in.evidence_media_id,
            student_notes=claim_in.student_notes,
        )
        db.add(progress)

    await db.commit()
    await db.refresh(progress)
    return progress
