from fastapi import APIRouter
from services.academy_service.routers._shared import *  # noqa: F401, F403

router = APIRouter(tags=["academy"])
logger = get_logger(__name__)


# ============================================================================
# COACH DASHBOARD ENDPOINTS
# ============================================================================


@router.get("/coach/me/dashboard", response_model=CoachDashboardSummary)
async def get_coach_dashboard(
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get dashboard summary for the current coach.

    Includes:
    - Cohort counts (active, upcoming, completed)
    - Student counts and pending approvals
    - Pending milestone reviews
    - Next upcoming session
            - Earnings summary
    """
    # 1. Resolve Member ID via members-service
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    member_id = member["id"]

    # 2. Get coach profile for rate info
    coach_profile = await get_coach_profile(str(member_id), calling_service="academy")
    stipend = coach_profile["academy_cohort_stipend"] if coach_profile else 0

    # 3. Get all cohorts for this coach with program info
    cohorts_query = (
        select(Cohort)
        .where(Cohort.coach_id == member_id)
        .options(selectinload(Cohort.program))
    )
    cohorts_result = await db.execute(cohorts_query)
    cohorts = cohorts_result.scalars().all()

    # 4. Count cohorts by status
    active_cohorts = 0
    upcoming_cohorts = 0
    completed_cohorts = 0
    current_period_earnings = 0
    cohort_ids = []

    now = utc_now()
    for cohort in cohorts:
        cohort_ids.append(cohort.id)
        if cohort.status == CohortStatus.ACTIVE:
            active_cohorts += 1
            current_period_earnings += stipend or 0
        elif cohort.status == CohortStatus.OPEN and cohort.start_date > now:
            upcoming_cohorts += 1
        elif cohort.status == CohortStatus.COMPLETED:
            completed_cohorts += 1

    # 5. Get student counts
    total_students = 0
    students_pending = 0
    if cohort_ids:
        students_query = select(
            func.count(Enrollment.id)
            .filter(Enrollment.status == EnrollmentStatus.ENROLLED)
            .label("enrolled"),
            func.count(Enrollment.id)
            .filter(Enrollment.status == EnrollmentStatus.PENDING_APPROVAL)
            .label("pending"),
        ).where(Enrollment.cohort_id.in_(cohort_ids))
        students_result = await db.execute(students_query)
        counts = students_result.mappings().first()
        if counts:
            total_students = counts["enrolled"] or 0
            students_pending = counts["pending"] or 0

    # 6. Count pending milestone reviews
    pending_reviews = 0
    if cohort_ids:
        # Get enrollment IDs for these cohorts
        enrollment_ids_query = select(Enrollment.id).where(
            Enrollment.cohort_id.in_(cohort_ids),
            Enrollment.status == EnrollmentStatus.ENROLLED,
        )
        enrollment_ids_result = await db.execute(enrollment_ids_query)
        enrollment_ids = [row[0] for row in enrollment_ids_result.fetchall()]

        if enrollment_ids:
            pending_query = select(func.count(StudentProgress.id)).where(
                StudentProgress.enrollment_id.in_(enrollment_ids),
                StudentProgress.status == ProgressStatus.PENDING,
                StudentProgress.evidence_media_id.isnot(None),  # Has submitted evidence
            )
            pending_result = await db.execute(pending_query)
            pending_reviews = pending_result.scalar() or 0

    # 7. Find next upcoming session (simplified - based on active cohorts)
    next_session = None
    if active_cohorts > 0:
        # Get the first active cohort as a proxy for next session
        for cohort in cohorts:
            if cohort.status == CohortStatus.ACTIVE:
                # Get enrolled count for this cohort
                enrolled_query = select(func.count(Enrollment.id)).where(
                    Enrollment.cohort_id == cohort.id,
                    Enrollment.status == EnrollmentStatus.ENROLLED,
                )
                enrolled_result = await db.execute(enrolled_query)
                enrolled_count = enrolled_result.scalar() or 0

                next_session = UpcomingSessionSummary(
                    cohort_id=cohort.id,
                    cohort_name=cohort.name,
                    program_name=cohort.program.name if cohort.program else None,
                    session_date=cohort.start_date,  # Placeholder - would need session model
                    location_name=cohort.location_name,
                    enrolled_count=enrolled_count,
                )
                break

    return CoachDashboardSummary(
        active_cohorts=active_cohorts,
        upcoming_cohorts=upcoming_cohorts,
        completed_cohorts=completed_cohorts,
        total_students=total_students,
        students_pending_approval=students_pending,
        pending_milestone_reviews=pending_reviews,
        upcoming_sessions_count=active_cohorts,  # Simplified
        next_session=next_session,
        current_period_earnings=current_period_earnings,
        pending_payout=current_period_earnings,  # Placeholder
    )


@router.get("/coach/me/cohorts/{cohort_id}", response_model=CoachCohortDetail)
async def get_coach_cohort_detail(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get detailed view of a specific cohort for the coach dashboard.

    Includes enrollment stats, progress tracking, and complexity score info.
    """
    # Verify coach owns this cohort
    await require_coach_for_cohort(current_user, str(cohort_id), db)

    # Get cohort with program
    cohort_query = (
        select(Cohort)
        .where(Cohort.id == cohort_id)
        .options(
            selectinload(Cohort.program),
            selectinload(Cohort.complexity_score),
        )
    )
    cohort_result = await db.execute(cohort_query)
    cohort = cohort_result.scalar_one_or_none()

    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    # Get enrollment counts
    enrolled_query = select(func.count(Enrollment.id)).where(
        Enrollment.cohort_id == cohort_id,
        Enrollment.status == EnrollmentStatus.ENROLLED,
    )
    waitlist_query = select(func.count(Enrollment.id)).where(
        Enrollment.cohort_id == cohort_id,
        Enrollment.status == EnrollmentStatus.WAITLIST,
    )

    enrolled_count = (await db.execute(enrolled_query)).scalar() or 0
    waitlist_count = (await db.execute(waitlist_query)).scalar() or 0

    # Get milestone progress
    milestones_count = 0
    milestones_achieved = 0

    if cohort.program:
        milestones_query = select(func.count(Milestone.id)).where(
            Milestone.program_id == cohort.program_id
        )
        milestones_count = (await db.execute(milestones_query)).scalar() or 0

        if milestones_count > 0 and enrolled_count > 0:
            # Get enrollment IDs
            enrollment_ids_query = select(Enrollment.id).where(
                Enrollment.cohort_id == cohort_id,
                Enrollment.status == EnrollmentStatus.ENROLLED,
            )
            enrollment_ids = [
                row[0] for row in (await db.execute(enrollment_ids_query)).fetchall()
            ]

            if enrollment_ids:
                achieved_query = select(func.count(StudentProgress.id)).where(
                    StudentProgress.enrollment_id.in_(enrollment_ids),
                    StudentProgress.status == ProgressStatus.ACHIEVED,
                )
                milestones_achieved = (await db.execute(achieved_query)).scalar() or 0

    # Calculate weeks completed
    now = utc_now()
    total_weeks = cohort.program.duration_weeks if cohort.program else 0
    weeks_completed = 0
    if cohort.start_date and total_weeks > 0:
        days_elapsed = (now - cohort.start_date).days
        weeks_completed = min(max(0, days_elapsed // 7), total_weeks)

    # Get complexity score info
    pay_band_min = None
    pay_band_max = None
    required_grade = cohort.required_coach_grade

    if cohort.complexity_score:
        pay_band_min = cohort.complexity_score.pay_band_min
        pay_band_max = cohort.complexity_score.pay_band_max
        required_grade = cohort.complexity_score.required_coach_grade

    return CoachCohortDetail(
        id=cohort.id,
        name=cohort.name,
        program_id=cohort.program_id,
        program_name=cohort.program.name if cohort.program else "Unknown",
        program_level=cohort.program.level.value if cohort.program else None,
        status=cohort.status,
        start_date=cohort.start_date,
        end_date=cohort.end_date,
        capacity=cohort.capacity,
        enrolled_count=enrolled_count,
        waitlist_count=waitlist_count,
        location_name=cohort.location_name,
        location_address=cohort.location_address,
        required_grade=required_grade,
        pay_band_min=pay_band_min,
        pay_band_max=pay_band_max,
        weeks_completed=weeks_completed,
        total_weeks=total_weeks,
        milestones_count=milestones_count,
        milestones_achieved_count=milestones_achieved,
    )


@router.get("/coach/me/students", response_model=List[EnrollmentResponse])
async def list_my_coach_students(
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """List all students across all cohorts where the current user is the assigned coach.

    Returns enrollments with cohort, program, and progress data.
    """

    # 1. Resolve Member ID via members-service
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    # 2. Get all cohorts for this coach
    cohorts_query = select(Cohort.id).where(Cohort.coach_id == member["id"])
    cohorts_result = await db.execute(cohorts_query)
    cohort_ids = [row[0] for row in cohorts_result.fetchall()]

    if not cohort_ids:
        return []

    # 3. Get all enrollments from those cohorts with progress data
    query = (
        select(Enrollment)
        .where(
            Enrollment.cohort_id.in_(cohort_ids),
            Enrollment.status.in_(
                [EnrollmentStatus.ENROLLED, EnrollmentStatus.PENDING_APPROVAL]
            ),
        )
        .options(
            selectinload(Enrollment.progress_records),
            joinedload(Enrollment.cohort).joinedload(Cohort.program),
            joinedload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
        .order_by(Enrollment.created_at.desc())
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


@router.get("/coach/me/earnings")
async def get_my_coach_earnings(
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """Get earnings summary for the current coach.

    Calculates earnings based on:
    - academy_cohort_stipend from CoachProfile
    - Number of active/completed cohorts
    """

    # 1. Resolve Member ID via members-service
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    member_id = member["id"]

    # 2. Get coach profile for rate information
    coach_profile = await get_coach_profile(str(member_id), calling_service="academy")

    stipend = coach_profile["academy_cohort_stipend"] if coach_profile else 0
    one_to_one_rate = coach_profile["one_to_one_rate_per_hour"] if coach_profile else 0
    group_rate = coach_profile["group_session_rate_per_hour"] if coach_profile else 0

    # 3. Get all cohorts for this coach
    query = (
        select(Cohort)
        .where(Cohort.coach_id == member_id)
        .options(selectinload(Cohort.program))
    )
    result = await db.execute(query)
    cohorts = result.scalars().all()

    # 4. Calculate earnings per cohort
    cohort_earnings = []
    total_earnings = 0
    active_cohorts = 0
    completed_cohorts = 0

    for cohort in cohorts:
        if cohort.status == CohortStatus.ACTIVE:
            active_cohorts += 1
            # Active cohorts earn the stipend
            earnings = stipend or 0
        elif cohort.status == CohortStatus.COMPLETED:
            completed_cohorts += 1
            # Completed cohorts have earned the stipend
            earnings = stipend or 0
        else:
            # Open/cancelled cohorts don't earn
            earnings = 0

        if earnings > 0:
            total_earnings += earnings
            cohort_earnings.append(
                {
                    "cohort_id": str(cohort.id),
                    "cohort_name": cohort.name
                    or (cohort.program.name if cohort.program else "Unnamed"),
                    "program_name": cohort.program.name if cohort.program else None,
                    "status": cohort.status.value,
                    "start_date": (
                        cohort.start_date.isoformat() if cohort.start_date else None
                    ),
                    "end_date": (
                        cohort.end_date.isoformat() if cohort.end_date else None
                    ),
                    "earnings": earnings,
                }
            )

    return {
        "summary": {
            "total_earnings": total_earnings,
            "active_cohorts": active_cohorts,
            "completed_cohorts": completed_cohorts,
            "pending_payout": total_earnings,  # Placeholder - would need payout tracking
        },
        "rates": {
            "academy_cohort_stipend": stipend,
            "one_to_one_rate_per_hour": one_to_one_rate,
            "group_session_rate_per_hour": group_rate,
        },
        "cohort_earnings": cohort_earnings,
    }


@router.get("/coach/me/resources", response_model=List[CohortResourceResponse])
async def list_my_coach_resources(
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """List all resources across all cohorts where the current user is the assigned coach."""

    # 1. Resolve Member ID via members-service
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    # 2. Get all cohorts for this coach
    cohorts_query = select(Cohort.id).where(Cohort.coach_id == member["id"])
    cohorts_result = await db.execute(cohorts_query)
    cohort_ids = [row[0] for row in cohorts_result.fetchall()]

    if not cohort_ids:
        return []

    # 3. Get all resources from those cohorts
    query = (
        select(CohortResource)
        .where(CohortResource.cohort_id.in_(cohort_ids))
        .options(joinedload(CohortResource.cohort))
        .order_by(CohortResource.created_at.desc())
    )
    result = await db.execute(query)
    return result.unique().scalars().all()


@router.get("/coach/me/pending-reviews", response_model=List[PendingMilestoneReview])
async def list_pending_milestone_reviews(
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all milestone claims waiting for coach review.

    Returns claims that have evidence submitted but haven't been reviewed yet.
    """

    # 1. Resolve Member ID via members-service
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    member_id = member["id"]

    # 2. Get cohort IDs for this coach
    cohorts_query = select(Cohort.id).where(Cohort.coach_id == member_id)
    cohorts_result = await db.execute(cohorts_query)
    cohort_ids = [row[0] for row in cohorts_result.fetchall()]

    if not cohort_ids:
        return []

    # 3. Get enrollment IDs
    enrollment_ids_query = select(
        Enrollment.id, Enrollment.member_id, Enrollment.cohort_id
    ).where(
        Enrollment.cohort_id.in_(cohort_ids),
        Enrollment.status == EnrollmentStatus.ENROLLED,
    )
    enrollment_ids_result = await db.execute(enrollment_ids_query)
    enrollments = {
        row[0]: {"member_id": row[1], "cohort_id": row[2]}
        for row in enrollment_ids_result.fetchall()
    }

    if not enrollments:
        return []

    # 4. Get pending progress records with evidence
    query = (
        select(StudentProgress)
        .where(
            StudentProgress.enrollment_id.in_(enrollments.keys()),
            StudentProgress.status == ProgressStatus.PENDING,
            StudentProgress.evidence_media_id.isnot(None),
        )
        .options(joinedload(StudentProgress.milestone))
        .order_by(StudentProgress.created_at.asc())
    )
    result = await db.execute(query)
    progress_records = result.unique().scalars().all()

    # 5. Get member and cohort info for display
    member_ids = list(
        set(enrollments[p.enrollment_id]["member_id"] for p in progress_records)
    )
    if member_ids:
        members_list = await get_members_bulk(
            [str(mid) for mid in member_ids], calling_service="academy"
        )
        members_map = {m["id"]: m for m in members_list}
    else:
        members_map = {}

    cohort_ids_for_display = list(
        set(enrollments[p.enrollment_id]["cohort_id"] for p in progress_records)
    )
    if cohort_ids_for_display:
        cohorts_query = select(Cohort.id, Cohort.name).where(
            Cohort.id.in_(cohort_ids_for_display)
        )
        cohorts_result = await db.execute(cohorts_query)
        cohorts_map = {row[0]: row[1] for row in cohorts_result.fetchall()}
    else:
        cohorts_map = {}

    # 6. Build response
    reviews = []
    for progress in progress_records:
        enrollment_info = enrollments[progress.enrollment_id]
        member_info = members_map.get(str(enrollment_info["member_id"]), {})
        cohort_name = cohorts_map.get(enrollment_info["cohort_id"], "Unknown")

        reviews.append(
            PendingMilestoneReview(
                progress_id=progress.id,
                enrollment_id=progress.enrollment_id,
                milestone_id=progress.milestone_id,
                milestone_name=(
                    progress.milestone.name if progress.milestone else "Unknown"
                ),
                milestone_type=(
                    progress.milestone.milestone_type.value
                    if progress.milestone
                    else "skill"
                ),
                student_member_id=enrollment_info["member_id"],
                student_name=f"{member_info.get('first_name', '')} {member_info.get('last_name', '')}".strip()
                or "Unknown",
                student_email=member_info.get("email"),
                cohort_id=enrollment_info["cohort_id"],
                cohort_name=cohort_name,
                evidence_media_id=progress.evidence_media_id,
                student_notes=progress.student_notes,
                claimed_at=progress.created_at,
            )
        )

    return reviews


@router.post("/coach/me/milestone-reviews/{progress_id}")
async def review_milestone_claim(
    progress_id: uuid.UUID,
    action: MilestoneReviewAction,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Review (approve/reject) a milestone claim.

    Only the coach assigned to the student's cohort can review.
    """
    # 1. Resolve Member ID via members-service
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    member_id = member["id"]

    # 2. Get the progress record
    progress_query = (
        select(StudentProgress)
        .options(selectinload(StudentProgress.enrollment))
        .where(StudentProgress.id == progress_id)
    )
    progress_result = await db.execute(progress_query)
    progress = progress_result.scalar_one_or_none()

    if not progress:
        raise HTTPException(status_code=404, detail="Progress record not found")

    # 3. Verify coach is assigned to this cohort
    cohort_query = select(Cohort.coach_id).where(
        Cohort.id == progress.enrollment.cohort_id
    )
    cohort_result = await db.execute(cohort_query)
    coach_id = cohort_result.scalar_one_or_none()

    if str(coach_id) != str(member_id):
        raise HTTPException(
            status_code=403, detail="Not authorized to review this milestone"
        )

    # 4. Perform the review action
    if action.action == "approve":
        progress.status = ProgressStatus.ACHIEVED
        progress.achieved_at = utc_now()
    elif action.action == "reject":
        progress.status = ProgressStatus.PENDING
        # Keep evidence_media_id for audit trail - student will replace it on resubmission
    else:
        raise HTTPException(
            status_code=400, detail="Invalid action. Use 'approve' or 'reject'"
        )

    progress.reviewed_by_coach_id = member_id
    progress.reviewed_at = utc_now()
    progress.score = action.score
    progress.coach_notes = action.coach_notes

    await db.commit()

    return {
        "message": f"Milestone {action.action}d successfully",
        "progress_id": str(progress_id),
        "status": progress.status.value,
    }
