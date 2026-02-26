from fastapi import APIRouter
from services.academy_service.routers._shared import (
    AsyncSession,
    AuthUser,
    CoachAssignment,
    Cohort,
    Depends,
    Enrollment,
    EnrollmentResponse,
    EnrollmentStatus,
    HTTPException,
    List,
    Milestone,
    NextSessionInfo,
    OnboardingResponse,
    _sync_installment_state_for_enrollment,
    get_async_db,
    get_current_user,
    get_logger,
    get_member_by_id,
    get_next_session_for_cohort,
    require_admin,
    select,
    selectinload,
    utc_now,
    uuid,
)

router = APIRouter(tags=["academy"])
logger = get_logger(__name__)


# --- Internal Service-to-Service Endpoints ---


@router.get(
    "/internal/coaches/{coach_member_id}/cohort-ids",
    response_model=List[uuid.UUID],
)
async def list_cohort_ids_for_coach(
    coach_member_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Internal helper endpoint used by sessions-service.

    Returns cohort IDs where the given coach is assigned (legacy cohort.coach_id
    or active lead/assistant coach assignments).
    """
    cohort_id_rows = await db.execute(
        select(Cohort.id).where(Cohort.coach_id == coach_member_id)
    )
    legacy_ids = {row[0] for row in cohort_id_rows.fetchall()}

    assignment_rows = await db.execute(
        select(CoachAssignment.cohort_id)
        .where(CoachAssignment.coach_id == coach_member_id)
        .where(CoachAssignment.status == "active")
        .where(CoachAssignment.role.in_(["lead", "assistant"]))
    )
    assigned_ids = {row[0] for row in assignment_rows.fetchall()}

    return sorted(legacy_ids | assigned_ids)


@router.get("/internal/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def get_enrollment_internal(
    enrollment_id: uuid.UUID,
    use_installments: bool = False,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a single enrollment by ID (internal service-to-service call).
    Used by payments service to lookup enrollment details.
    No auth required as this is called with service role token.

    Pass ``?use_installments=true`` when the member opted for an installment
    plan at checkout — this triggers schedule creation if none exists yet.
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
    await _sync_installment_state_for_enrollment(
        db, enrollment, use_installments=use_installments
    )
    await db.commit()
    return enrollment


@router.get("/internal/cohorts/{cohort_id}")
async def get_cohort_internal(
    cohort_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get basic cohort info (internal service-to-service call).
    Used by communications-service to validate coach ownership.
    """
    query = select(Cohort).where(Cohort.id == cohort_id)
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    return {
        "id": str(cohort.id),
        "name": cohort.name,
        "coach_id": str(cohort.coach_id) if cohort.coach_id else None,
        "program_id": str(cohort.program_id) if cohort.program_id else None,
        "status": cohort.status.value if cohort.status else None,
    }


@router.get("/internal/cohorts/{cohort_id}/enrolled-students")
async def get_cohort_enrolled_students_internal(
    cohort_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get enrolled students for a cohort (internal service-to-service call).
    Used by communications-service for messaging.
    Returns enrollment info with member_id (caller fetches member details separately).
    """
    query = select(Enrollment).where(
        Enrollment.cohort_id == cohort_id,
        Enrollment.status.in_(
            [
                EnrollmentStatus.ENROLLED,
            ]
        ),
    )
    result = await db.execute(query)
    enrollments = result.scalars().all()
    return [
        {
            "enrollment_id": str(e.id),
            "member_id": str(e.member_id),
            "status": e.status.value if e.status else None,
        }
        for e in enrollments
    ]


@router.get(
    "/my-enrollments/{enrollment_id}/onboarding", response_model=OnboardingResponse
)
async def get_enrollment_onboarding(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get structured onboarding information for a new enrollment.
    Returns next session, prep materials, and dashboard links.
    """
    # Fetch enrollment with relationships
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

    cohort = enrollment.cohort
    program = enrollment.program or (cohort.program if cohort else None)

    if not cohort or not program:
        raise HTTPException(
            status_code=400, detail="Enrollment missing cohort or program information"
        )

    # Get coach name if coach_id is set
    coach_name = None
    if cohort.coach_id:
        coach = await get_member_by_id(str(cohort.coach_id), calling_service="academy")
        if coach:
            coach_name = f"{coach['first_name']} {coach['last_name']}"

    # Find next session via sessions-service
    now = utc_now()
    next_session_data = await get_next_session_for_cohort(
        str(cohort.id), calling_service="academy"
    )

    if next_session_data:
        next_session = NextSessionInfo(
            date=next_session_data["starts_at"],
            location=next_session_data["location_name"],
            notes=f"Session: {next_session_data['title']}",
        )
    else:
        # Fallback to cohort start_date if no sessions scheduled yet
        next_session = NextSessionInfo(
            date=cohort.start_date if cohort.start_date > now else None,
            location=cohort.location_name,
            notes="Check your email for session schedule details.",
        )

    # Count milestones
    milestone_query = select(Milestone).where(Milestone.program_id == program.id)
    milestone_result = await db.execute(milestone_query)
    total_milestones = len(milestone_result.scalars().all())

    return OnboardingResponse(
        enrollment_id=enrollment.id,
        program_name=program.name,
        cohort_name=cohort.name,
        start_date=cohort.start_date,
        end_date=cohort.end_date,
        location=cohort.location_name,
        next_session=next_session if next_session.date else None,
        prep_materials=program.prep_materials,
        dashboard_link=f"/account/academy/enrollments/{enrollment.id}",
        resources_link=f"/account/academy/cohorts/{cohort.id}/resources",
        sessions_link="/account/sessions",
        coach_name=coach_name,
        total_milestones=total_milestones,
    )
