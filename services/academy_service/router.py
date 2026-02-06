import uuid
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import (
    _service_role_jwt,
    get_current_user,
    require_admin,
    require_coach,
    require_coach_for_cohort,
)
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_url, resolve_media_urls
from libs.db.session import get_async_db
from services.academy_service.models import (
    CoachGrade,
    Cohort,
    CohortComplexityScore,
    CohortResource,
    CohortStatus,
    Enrollment,
    EnrollmentStatus,
    Milestone,
    PaymentStatus,
    Program,
    ProgramCategory,
    ProgramInterest,
    ProgressStatus,
    StudentProgress,
)
from services.academy_service.schemas import (
    CoachCohortDetail,
    CoachDashboardSummary,
    CohortComplexityScoreCreate,
    CohortComplexityScoreResponse,
    CohortComplexityScoreUpdate,
    CohortCreate,
    CohortResourceResponse,
    CohortResponse,
    CohortUpdate,
    CohortWithScoreResponse,
    ComplexityScoreCalculation,
    EligibleCoachResponse,
    EnrollmentCreate,
    EnrollmentResponse,
    EnrollmentUpdate,
    MemberMilestoneClaimRequest,
    MilestoneCreate,
    MilestoneResponse,
    MilestoneReviewAction,
    NextSessionInfo,
    OnboardingResponse,
    PendingMilestoneReview,
    ProgramCreate,
    ProgramResponse,
    ProgramUpdate,
    StudentProgressResponse,
    StudentProgressUpdate,
    UpcomingSessionSummary,
)
from services.academy_service.scoring import (
    calculate_complexity_score,
    get_dimension_labels,
    is_coach_eligible_for_grade,
)
from services.members_service.models import Member
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(tags=["academy"])
logger = get_logger(__name__)


async def _ensure_active_coach(db: AsyncSession, coach_member_id: uuid.UUID) -> None:
    result = await db.execute(
        text("SELECT status FROM coach_profiles WHERE member_id = :member_id"),
        {"member_id": coach_member_id},
    )
    status = result.scalar_one_or_none()
    if status is None:
        raise HTTPException(status_code=400, detail="Coach profile not found")
    if status != "active":
        raise HTTPException(
            status_code=400,
            detail="Coach must complete onboarding before assignment",
        )


# --- Admin Tasks ---


@router.post("/admin/tasks/transition-cohort-statuses")
async def trigger_cohort_status_transitions(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Manually trigger cohort status transitions (OPEN→ACTIVE, ACTIVE→COMPLETED).
    Useful for testing or manual corrections.
    """
    from services.academy_service.tasks import transition_cohort_statuses

    await transition_cohort_statuses()
    return {"message": "Cohort status transitions triggered successfully"}


# --- Programs ---


@router.post("/programs", response_model=ProgramResponse)
async def create_program(
    program_in: ProgramCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    program = Program(**program_in.model_dump())
    db.add(program)
    await db.commit()
    await db.refresh(program)
    return program


@router.delete("/admin/members/{member_id}")
async def admin_delete_member_academy_records(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete academy enrollments and progress records for a member (Admin only).
    """
    enrollment_ids = (
        (
            await db.execute(
                select(Enrollment.id).where(Enrollment.member_id == member_id)
            )
        )
        .scalars()
        .all()
    )

    deleted_progress = 0
    if enrollment_ids:
        progress_result = await db.execute(
            delete(StudentProgress).where(
                StudentProgress.enrollment_id.in_(enrollment_ids)
            )
        )
        deleted_progress = progress_result.rowcount or 0

    enrollment_result = await db.execute(
        delete(Enrollment).where(Enrollment.member_id == member_id)
    )

    await db.commit()
    return {
        "deleted_enrollments": enrollment_result.rowcount or 0,
        "deleted_progress": deleted_progress,
    }


@router.get("/programs", response_model=List[ProgramResponse])
async def list_programs(
    published_only: bool = False,
    db: AsyncSession = Depends(get_async_db),
):
    """List all programs. Use published_only=true for member-facing views."""
    query = select(Program).order_by(Program.name)
    if published_only:
        query = query.where(Program.is_published.is_(True))
    result = await db.execute(query)
    programs = result.scalars().all()

    # Resolve cover image URLs
    media_ids = [p.cover_image_media_id for p in programs if p.cover_image_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    responses = []
    for program in programs:
        resp = ProgramResponse.model_validate(program).model_dump()
        if program.cover_image_media_id:
            resp["cover_image_url"] = url_map.get(program.cover_image_media_id)
        responses.append(resp)
    return responses


@router.get("/programs/published", response_model=List[ProgramResponse])
async def list_published_programs(
    db: AsyncSession = Depends(get_async_db),
):
    """List only published programs (for member-facing pages)."""
    query = select(Program).where(Program.is_published.is_(True)).order_by(Program.name)
    result = await db.execute(query)
    programs = result.scalars().all()

    # Resolve cover image URLs
    media_ids = [p.cover_image_media_id for p in programs if p.cover_image_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    responses = []
    for program in programs:
        resp = ProgramResponse.model_validate(program).model_dump()
        if program.cover_image_media_id:
            resp["cover_image_url"] = url_map.get(program.cover_image_media_id)
        responses.append(resp)
    return responses


@router.get("/programs/{program_id}", response_model=ProgramResponse)
async def get_program(
    program_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Program).where(Program.id == program_id)
    result = await db.execute(query)
    program = result.scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    # Resolve cover image URL
    resp = ProgramResponse.model_validate(program).model_dump()
    resp["cover_image_url"] = await resolve_media_url(program.cover_image_media_id)
    return resp


@router.put("/programs/{program_id}", response_model=ProgramResponse)
async def update_program(
    program_id: uuid.UUID,
    program_in: ProgramUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Program).where(Program.id == program_id)
    result = await db.execute(query)
    program = result.scalar_one_or_none()

    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    update_data = program_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(program, field, value)

    await db.commit()
    await db.refresh(program)

    # Resolve cover image URL
    resp = ProgramResponse.model_validate(program).model_dump()
    resp["cover_image_url"] = await resolve_media_url(program.cover_image_media_id)
    return resp


@router.delete("/programs/{program_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_program(
    program_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Program).where(Program.id == program_id)
    result = await db.execute(query)
    program = result.scalar_one_or_none()

    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    await db.delete(program)
    await db.commit()
    return None


# --- Cohorts ---


@router.post("/cohorts", response_model=CohortResponse)
async def create_cohort(
    cohort_in: CohortCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    from sqlalchemy.orm import selectinload

    if cohort_in.coach_id:
        await _ensure_active_coach(db, cohort_in.coach_id)

    # Extract coach_assignments before creating cohort (not a DB field)
    coach_assignments_input = cohort_in.coach_assignments
    cohort_data = cohort_in.model_dump(exclude={"coach_assignments"})
    cohort = Cohort(**cohort_data)
    db.add(cohort)
    await db.flush()  # Get cohort.id before creating assignments

    # Get admin member ID for assigned_by_id
    from sqlalchemy import text as sa_text

    admin_row = await db.execute(
        sa_text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": current_user.user_id},
    )
    admin_member = admin_row.mappings().first()
    admin_id = admin_member["id"] if admin_member else None

    # Create CoachAssignment records
    if coach_assignments_input:
        from services.academy_service.models import CoachAssignment

        for ca_input in coach_assignments_input:
            assignment = CoachAssignment(
                cohort_id=cohort.id,
                coach_id=ca_input.coach_id,
                role=ca_input.role,
                assigned_by_id=admin_id,
                status="active",
            )
            db.add(assignment)

            # Set cohort.coach_id for backward compat when lead is assigned
            if ca_input.role == "lead":
                cohort.coach_id = ca_input.coach_id
    elif cohort_in.coach_id:
        # Legacy: if coach_id provided without coach_assignments, create lead assignment
        from services.academy_service.models import CoachAssignment

        assignment = CoachAssignment(
            cohort_id=cohort.id,
            coach_id=cohort_in.coach_id,
            role="lead",
            assigned_by_id=admin_id,
            status="active",
        )
        db.add(assignment)

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
    from sqlalchemy.orm import selectinload

    query = (
        select(Cohort)
        .where(Cohort.id == cohort_id)
        .options(selectinload(Cohort.program))
    )
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()

    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    # Track if coach is being newly assigned
    old_coach_id = cohort.coach_id
    update_data = cohort_in.model_dump(exclude_unset=True)
    new_coach_id = update_data.get("coach_id")

    if new_coach_id is not None:
        await _ensure_active_coach(db, new_coach_id)

    for field, value in update_data.items():
        setattr(cohort, field, value)

    await db.commit()

    # Send notification if a new coach is being assigned
    if new_coach_id and new_coach_id != old_coach_id:
        try:
            # Get coach member details
            coach_row = await db.execute(
                text(
                    "SELECT email, first_name, last_name FROM members WHERE id = :coach_id"
                ),
                {"coach_id": new_coach_id},
            )
            coach = coach_row.mappings().first()

            if coach and coach["email"]:
                # Count enrolled students
                enrolled_count_result = await db.execute(
                    select(func.count(Enrollment.id)).where(
                        Enrollment.cohort_id == cohort_id,
                        Enrollment.status == EnrollmentStatus.ENROLLED,
                    )
                )
                student_count = enrolled_count_result.scalar() or 0

                email_client = get_email_client()
                await email_client.send_template(
                    template_type="coach_assignment",
                    to_email=coach["email"],
                    template_data={
                        "coach_name": f"{coach['first_name']} {coach['last_name']}",
                        "program_name": (
                            cohort.program.name if cohort.program else "Unknown Program"
                        ),
                        "cohort_name": cohort.name,
                        "start_date": cohort.start_date.strftime("%b %d, %Y"),
                    },
                )
                logger.info(
                    f"Sent coach assignment email to {coach['email']} for cohort {cohort.name}"
                )
        except Exception as e:
            logger.error(f"Failed to send coach assignment email: {e}")
            # Don't fail the update if email fails

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

    await db.delete(cohort)
    await db.commit()
    return None


@router.get("/cohorts", response_model=List[CohortResponse])
async def list_cohorts(
    program_id: uuid.UUID = None,
    db: AsyncSession = Depends(get_async_db),
):
    from sqlalchemy.orm import selectinload

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
    from sqlalchemy.orm import selectinload

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
    from sqlalchemy.orm import selectinload

    # 1. Resolve Member ID (lookup by auth_id)
    member_row = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": current_user.user_id},
    )
    member = member_row.mappings().first()

    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    # 2. Query Cohorts
    query = (
        select(Cohort)
        .where(Cohort.coach_id == member["id"])
        .options(selectinload(Cohort.program))
        .order_by(Cohort.start_date.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/coach/me/students", response_model=List[EnrollmentResponse])
async def list_my_coach_students(
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """List all students across all cohorts where the current user is the assigned coach.

    Returns enrollments with cohort, program, and progress data.
    """
    from sqlalchemy.orm import joinedload, selectinload

    # 1. Resolve Member ID (lookup by auth_id)
    member_row = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": current_user.user_id},
    )
    member = member_row.mappings().first()

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
        )
        .order_by(Enrollment.created_at.desc())
    )
    result = await db.execute(query)
    return result.unique().scalars().all()


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
    from sqlalchemy.orm import selectinload

    # 1. Resolve Member ID (lookup by auth_id)
    member_row = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": current_user.user_id},
    )
    member = member_row.mappings().first()

    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    member_id = member["id"]

    # 2. Get coach profile for rate information
    coach_profile_row = await db.execute(
        text(
            "SELECT academy_cohort_stipend, one_to_one_rate_per_hour, group_session_rate_per_hour "
            "FROM coach_profiles WHERE member_id = :member_id"
        ),
        {"member_id": member_id},
    )
    coach_profile = coach_profile_row.mappings().first()

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
    from sqlalchemy.orm import joinedload

    # 1. Resolve Member ID (lookup by auth_id)
    member_row = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": current_user.user_id},
    )
    member = member_row.mappings().first()

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
    from datetime import timedelta

    from sqlalchemy.orm import joinedload

    # 1. Resolve Member ID
    member_row = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": current_user.user_id},
    )
    member = member_row.mappings().first()
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    member_id = member["id"]

    # 2. Get coach profile for rate info
    coach_profile_row = await db.execute(
        text(
            "SELECT academy_cohort_stipend FROM coach_profiles WHERE member_id = :member_id"
        ),
        {"member_id": member_id},
    )
    coach_profile = coach_profile_row.mappings().first()
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


@router.get("/coach/me/pending-reviews", response_model=List[PendingMilestoneReview])
async def list_pending_milestone_reviews(
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all milestone claims waiting for coach review.

    Returns claims that have evidence submitted but haven't been reviewed yet.
    """
    from sqlalchemy.orm import joinedload

    # 1. Resolve Member ID
    member_row = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": current_user.user_id},
    )
    member = member_row.mappings().first()
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
        members_query = await db.execute(
            text(
                "SELECT id, first_name, last_name, email FROM members WHERE id = ANY(:ids)"
            ),
            {"ids": member_ids},
        )
        members_map = {row["id"]: row for row in members_query.mappings().all()}
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
        member_info = members_map.get(enrollment_info["member_id"], {})
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
    # 1. Resolve Member ID
    member_row = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": current_user.user_id},
    )
    member = member_row.mappings().first()
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

    if coach_id != member_id:
        raise HTTPException(
            status_code=403, detail="Not authorized to review this milestone"
        )

    # 4. Perform the review action
    if action.action == "approve":
        progress.status = ProgressStatus.ACHIEVED
        progress.achieved_at = utc_now()
    elif action.action == "reject":
        progress.status = ProgressStatus.PENDING
        progress.evidence_media_id = None  # Clear evidence so student can resubmit
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


@router.get("/cohorts/{cohort_id}", response_model=CohortResponse)
async def get_cohort(
    cohort_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    from sqlalchemy.orm import selectinload

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

    # Eager load progress records, cohort, and program; member data is resolved by ID externally
    from sqlalchemy.orm import joinedload, selectinload

    query = (
        select(Enrollment)
        .where(Enrollment.cohort_id == cohort_id)
        .options(
            selectinload(Enrollment.progress_records),
            joinedload(Enrollment.cohort).joinedload(Cohort.program),
            joinedload(Enrollment.program),
        )
    )
    result = await db.execute(query)
    return result.unique().scalars().all()


# --- Milestones ---


@router.post("/milestones", response_model=MilestoneResponse)
async def create_milestone(
    milestone_in: MilestoneCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    milestone = Milestone(**milestone_in.model_dump())
    db.add(milestone)
    await db.commit()
    await db.refresh(milestone)
    return milestone


@router.get("/programs/{program_id}/milestones", response_model=List[MilestoneResponse])
async def list_program_milestones(
    program_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    query = (
        select(Milestone)
        .where(Milestone.program_id == program_id)
        .order_by(Milestone.order_index)
    )
    result = await db.execute(query)
    return result.scalars().all()


# --- Enrollments ---


@router.post("/enrollments", response_model=EnrollmentResponse)
async def enroll_student(
    enrollment_in: EnrollmentCreate,
    current_user: AuthUser = Depends(require_admin),  # Admin can enroll anyone
    db: AsyncSession = Depends(get_async_db),
):
    # Check if already enrolled
    # Check if already enrolled in this program/cohort
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

    enrollment = Enrollment(
        program_id=enrollment_in.program_id,
        cohort_id=enrollment_in.cohort_id,
        member_id=enrollment_in.member_id,
        status=EnrollmentStatus.ENROLLED,  # Admin enrolls directly
        payment_status=PaymentStatus.PENDING,
        preferences=enrollment_in.preferences,
    )
    db.add(enrollment)
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


@router.get("/enrollments", response_model=List[EnrollmentResponse])
async def list_enrollments(
    status: Optional[EnrollmentStatus] = None,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all enrollments (admin only). Filter by status optional."""
    from sqlalchemy.orm import selectinload

    query = (
        select(Enrollment)
        .options(selectinload(Enrollment.cohort), selectinload(Enrollment.program))
        .order_by(Enrollment.created_at.desc())
    )

    if status:
        query = query.where(Enrollment.status == status)

    result = await db.execute(query)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def get_enrollment(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get detailed enrollment info."""
    from sqlalchemy.orm import selectinload

    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(selectinload(Enrollment.cohort), selectinload(Enrollment.program))
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

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

    # 1. Get Member ID
    member_row = await db.execute(
        text(
            "SELECT id, first_name, last_name, email FROM members WHERE auth_id = :auth_id"
        ),
        {"auth_id": current_user.user_id},
    )
    member = member_row.mappings().first()

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
        preferences=preferences or {},
    )
    db.add(enrollment)
    await db.commit()

    # Re-fetch with relationships to avoid lazy loading issues
    from sqlalchemy.orm import selectinload

    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment.id)
        .options(selectinload(Enrollment.cohort), selectinload(Enrollment.program))
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
        )
    )
    result = await db.execute(query)
    return result.scalars().all()


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
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    return enrollment


@router.get("/internal/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def get_enrollment_internal(
    enrollment_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a single enrollment by ID (internal service-to-service call).
    Used by payments service to lookup enrollment details.
    No auth required as this is called with service role token.
    """
    from sqlalchemy.orm import selectinload

    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    return enrollment


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
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    cohort = enrollment.cohort
    program = enrollment.program or (cohort.program if cohort else None)

    if not cohort or not program:
        raise HTTPException(
            status_code=400, detail="Enrollment missing cohort or program information"
        )

    # Get coach name if coach_id is set
    coach_name = None
    if cohort.coach_id:
        coach_row = await db.execute(
            text("SELECT first_name, last_name FROM members WHERE id = :coach_id"),
            {"coach_id": cohort.coach_id},
        )
        coach = coach_row.mappings().first()
        if coach:
            coach_name = f"{coach['first_name']} {coach['last_name']}"

    # Find next session (query sessions table directly)
    now = utc_now()
    next_session_row = await db.execute(
        text(
            """
            SELECT starts_at, title, location_name
            FROM sessions
            WHERE cohort_id = :cohort_id AND starts_at > :now
            ORDER BY starts_at ASC
            LIMIT 1
            """
        ),
        {"cohort_id": cohort.id, "now": now},
    )
    next_session_data = next_session_row.mappings().first()

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
        sessions_link="/sessions",
        coach_name=coach_name,
        total_milestones=total_milestones,
    )


@router.get("/cohorts/{cohort_id}/enrollments", response_model=List[EnrollmentResponse])
async def list_cohort_enrollments(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Enrollment).where(Enrollment.cohort_id == cohort_id)
    result = await db.execute(query)
    return result.scalars().all()


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


@router.get("/cohorts/{cohort_id}/progress-report.pdf")
async def download_cohort_progress_report(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Download a PDF progress report for a specific cohort.
    Contains all students and their milestone progress.
    """
    from fastapi.responses import Response
    from libs.common.pdf import generate_progress_report_pdf

    # Get cohort with program
    cohort_query = (
        select(Cohort)
        .options(selectinload(Cohort.program))
        .where(Cohort.id == cohort_id)
    )
    cohort_result = await db.execute(cohort_query)
    cohort = cohort_result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    program = cohort.program
    if not program:
        raise HTTPException(status_code=400, detail="Cohort has no associated program")

    # Get all milestones
    milestone_query = select(Milestone).where(Milestone.program_id == program.id)
    milestone_result = await db.execute(milestone_query)
    all_milestones = milestone_result.scalars().all()
    total_milestones = len(all_milestones)
    milestone_map = {m.id: m.name for m in all_milestones}

    # Get all enrollments with members
    enrollment_query = (
        select(Enrollment, Member)
        .join(Member, Enrollment.member_id == Member.id)
        .where(
            Enrollment.cohort_id == cohort_id,
            Enrollment.status == EnrollmentStatus.ENROLLED,
        )
    )
    enrollment_result = await db.execute(enrollment_query)
    enrollments = enrollment_result.all()

    if not enrollments:
        raise HTTPException(status_code=404, detail="No enrolled students found")

    # For cohort PDF, we'll generate for the first student as a demo
    # In a real implementation, you might want a cohort-level summary PDF
    enrollment, member = enrollments[0]

    # Get progress for this enrollment
    progress_query = select(StudentProgress).where(
        StudentProgress.enrollment_id == enrollment.id
    )
    progress_result = await db.execute(progress_query)
    all_progress = progress_result.scalars().all()

    completed_count = len([p for p in all_progress if p.status.value == "achieved"])
    milestone_data = [
        {
            "name": milestone_map.get(p.milestone_id, "Unknown"),
            "status": p.status.value if p.status else "pending",
            "achieved_at": p.achieved_at,
            "coach_notes": p.coach_notes,
        }
        for p in all_progress
    ]

    # Generate PDF
    pdf_bytes = generate_progress_report_pdf(
        student_name=f"{member.first_name} {member.last_name}",
        program_name=program.name,
        cohort_name=cohort.name,
        start_date=cohort.start_date,
        end_date=cohort.end_date,
        milestones=milestone_data,
        total_milestones=total_milestones,
        completed_milestones=completed_count,
        report_date=utc_now(),
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=progress_report_{cohort.name.replace(' ', '_')}.pdf"
        },
    )


@router.get("/enrollments/{enrollment_id}/certificate.pdf")
async def download_certificate(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Download completion certificate for an enrollment.
    Only available if all milestones are completed and certificate was issued.
    """
    from fastapi.responses import Response
    from libs.common.pdf import generate_certificate_pdf

    # Get enrollment with program and cohort
    query = (
        select(Enrollment)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
        )
        .where(Enrollment.id == enrollment_id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    # Verify ownership (unless admin)
    if str(enrollment.member_id) != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=403, detail="Not authorized to view this certificate"
        )

    # Check if certificate has been issued
    if not enrollment.certificate_issued_at or not enrollment.certificate_code:
        raise HTTPException(
            status_code=404,
            detail="Certificate not yet available. Complete all milestones to earn your certificate.",
        )

    # Get member name
    member_query = text(
        "SELECT first_name, last_name FROM members WHERE id = :member_id"
    )
    member_result = await db.execute(member_query, {"member_id": enrollment.member_id})
    member = member_result.mappings().first()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    student_name = f"{member['first_name']} {member['last_name']}"

    cohort = enrollment.cohort
    program = enrollment.program or (cohort.program if cohort else None)
    program_name = program.name if program else "SwimBuddz Program"

    # Generate PDF
    pdf_bytes = generate_certificate_pdf(
        student_name=student_name,
        program_name=program_name,
        completion_date=enrollment.certificate_issued_at,
        verification_code=enrollment.certificate_code,
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=certificate_{program_name.replace(' ', '_')}.pdf"
        },
    )


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
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    update_data = enrollment_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(enrollment, field, value)

    await db.commit()

    # Reload with relationships eager loaded to avoid lazy-load during response serialization
    refreshed = await db.execute(query)
    enrollment = refreshed.scalar_one_or_none()
    return enrollment


@router.post(
    "/admin/enrollments/{enrollment_id}/mark-paid", response_model=EnrollmentResponse
)
async def admin_mark_enrollment_paid(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Mark an enrollment as paid (service-to-service call from payments_service).
    Updates payment_status to PAID and enrollment status to ENROLLED if pending.
    """
    from sqlalchemy.orm import selectinload

    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(selectinload(Enrollment.cohort), selectinload(Enrollment.program))
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    # Update payment status to PAID
    enrollment.payment_status = PaymentStatus.PAID

    # If enrollment was pending approval, check if cohort requires manual approval
    if enrollment.status == EnrollmentStatus.PENDING_APPROVAL:
        cohort = enrollment.cohort
        # Only auto-promote if cohort doesn't require approval
        if not cohort or not cohort.require_approval:
            enrollment.status = EnrollmentStatus.ENROLLED

    await db.commit()

    # Send enrollment confirmation email
    try:
        # Get member email from members service
        settings = get_settings()
        headers = {"Authorization": f"Bearer {_service_role_jwt('academy')}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{settings.MEMBERS_SERVICE_URL}/members/{enrollment.member_id}",
                headers=headers,
            )
            if resp.status_code == 200:
                member_data = resp.json()
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

                    email_client = get_email_client()
                    await email_client.send_template(
                        template_type="enrollment_confirmation",
                        to_email=member_email,
                        template_data={
                            "member_name": member_name,
                            "program_name": program_name,
                            "cohort_name": cohort_name,
                            "start_date": start_date,
                        },
                    )
    except Exception as e:
        # Log but don't fail the request if email fails
        logger.warning(f"Failed to send enrollment confirmation email: {e}")

    # Re-fetch with relationships for response
    result = await db.execute(query)
    return result.scalar_one()


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
    enrollment_query = select(Enrollment).where(Enrollment.id == enrollment_id)
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

    # Check enrollment is approved and paid before allowing claims
    if enrollment.status == EnrollmentStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your enrollment is awaiting admin approval. You cannot claim milestones yet.",
        )

    if enrollment.payment_status != PaymentStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Payment is required before claiming milestones.",
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
        # Update existing record
        progress.status = ProgressStatus.ACHIEVED
        progress.achieved_at = utc_now()
        if claim_in.evidence_media_id:
            progress.evidence_media_id = claim_in.evidence_media_id
        if claim_in.student_notes:
            progress.student_notes = claim_in.student_notes
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


# --- Program Interest (Get Notified) ---


@router.post("/programs/{program_id}/interest")
async def register_program_interest(
    program_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Register interest in a program to be notified when new cohorts open.
    """
    # Verify program exists
    program_query = select(Program).where(Program.id == program_id)
    program_result = await db.execute(program_query)
    program = program_result.scalar_one_or_none()

    if not program:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Program not found",
        )

    # Get member info
    member_query = select(Member).where(Member.auth_id == current_user.user_id)
    member_result = await db.execute(member_query)
    member = member_result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    # Check if interest already exists
    existing_query = select(ProgramInterest).where(
        ProgramInterest.program_id == program_id,
        ProgramInterest.member_auth_id == current_user.user_id,
    )
    existing_result = await db.execute(existing_query)
    existing = existing_result.scalar_one_or_none()

    if existing:
        return {
            "message": "You're already registered to receive notifications for this program",
            "registered": True,
        }

    # Create interest record
    interest = ProgramInterest(
        program_id=program_id,
        member_id=member.id,
        member_auth_id=current_user.user_id,
        email=member.email,
    )
    db.add(interest)
    await db.commit()

    return {
        "message": f"Great! You'll be notified when new cohorts for '{program.name}' open.",
        "registered": True,
    }


@router.delete("/programs/{program_id}/interest")
async def remove_program_interest(
    program_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Remove interest in a program (unsubscribe from notifications).
    """
    query = select(ProgramInterest).where(
        ProgramInterest.program_id == program_id,
        ProgramInterest.member_auth_id == current_user.user_id,
    )
    result = await db.execute(query)
    interest = result.scalar_one_or_none()

    if not interest:
        return {"message": "Not registered for notifications", "registered": False}

    await db.delete(interest)
    await db.commit()

    return {
        "message": "You've been unsubscribed from notifications",
        "registered": False,
    }


@router.get("/programs/{program_id}/interest")
async def check_program_interest(
    program_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Check if the current user is registered for notifications for a program.
    """
    query = select(ProgramInterest).where(
        ProgramInterest.program_id == program_id,
        ProgramInterest.member_auth_id == current_user.user_id,
    )
    result = await db.execute(query)
    interest = result.scalar_one_or_none()

    return {"registered": interest is not None}


# ============================================================================
# COHORT COMPLEXITY SCORING
# ============================================================================


@router.post("/scoring/calculate", response_model=ComplexityScoreCalculation)
async def preview_complexity_score(
    category: ProgramCategory,
    dimension_scores: List[int],
    _: AuthUser = Depends(require_admin),
):
    """
    Preview complexity score calculation without saving.
    Useful for testing scores before committing to a cohort.
    """
    if len(dimension_scores) != 7:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Exactly 7 dimension scores required",
        )

    for i, score in enumerate(dimension_scores):
        if score < 1 or score > 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Dimension {i + 1} score must be between 1 and 5",
            )

    try:
        result = calculate_complexity_score(category, dimension_scores)
        return ComplexityScoreCalculation(**result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/scoring/dimensions/{category}")
async def get_scoring_dimensions(
    category: ProgramCategory,
    _: AuthUser = Depends(require_admin),
):
    """
    Get the dimension labels for a specific program category.
    Useful for building the scoring UI.
    """
    try:
        labels = get_dimension_labels(category)
        return {
            "category": category,
            "dimensions": [
                {"number": i + 1, "label": label} for i, label in enumerate(labels)
            ],
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/cohorts/{cohort_id}/complexity-score",
    response_model=CohortComplexityScoreResponse,
)
async def create_cohort_complexity_score(
    cohort_id: uuid.UUID,
    score_data: CohortComplexityScoreCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a complexity score for a cohort.
    This determines the required coach grade and pay band.
    """
    # Check cohort exists
    result = await db.execute(select(Cohort).where(Cohort.id == cohort_id))
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    # Check if score already exists
    result = await db.execute(
        select(CohortComplexityScore).where(
            CohortComplexityScore.cohort_id == cohort_id
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Complexity score already exists for this cohort. Use PUT to update.",
        )

    # Get member_id from auth user
    member_result = await db.execute(
        select(Member.id).where(Member.auth_id == current_user.user_id)
    )
    member_id = member_result.scalar_one_or_none()
    if not member_id:
        raise HTTPException(status_code=404, detail="Member not found")

    # Extract dimension scores
    dimension_scores = [
        score_data.dimension_1.score,
        score_data.dimension_2.score,
        score_data.dimension_3.score,
        score_data.dimension_4.score,
        score_data.dimension_5.score,
        score_data.dimension_6.score,
        score_data.dimension_7.score,
    ]

    # Calculate score
    try:
        calc_result = calculate_complexity_score(score_data.category, dimension_scores)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Create the score record
    complexity_score = CohortComplexityScore(
        cohort_id=cohort_id,
        category=score_data.category,
        dimension_1_score=score_data.dimension_1.score,
        dimension_1_rationale=score_data.dimension_1.rationale,
        dimension_2_score=score_data.dimension_2.score,
        dimension_2_rationale=score_data.dimension_2.rationale,
        dimension_3_score=score_data.dimension_3.score,
        dimension_3_rationale=score_data.dimension_3.rationale,
        dimension_4_score=score_data.dimension_4.score,
        dimension_4_rationale=score_data.dimension_4.rationale,
        dimension_5_score=score_data.dimension_5.score,
        dimension_5_rationale=score_data.dimension_5.rationale,
        dimension_6_score=score_data.dimension_6.score,
        dimension_6_rationale=score_data.dimension_6.rationale,
        dimension_7_score=score_data.dimension_7.score,
        dimension_7_rationale=score_data.dimension_7.rationale,
        total_score=calc_result["total_score"],
        required_coach_grade=calc_result["required_coach_grade"],
        pay_band_min=calc_result["pay_band_min"],
        pay_band_max=calc_result["pay_band_max"],
        scored_by_id=member_id,
        scored_at=utc_now(),
    )

    db.add(complexity_score)

    # Update cohort with required grade
    cohort.required_coach_grade = calc_result["required_coach_grade"]

    await db.commit()
    await db.refresh(complexity_score)

    return complexity_score


@router.get(
    "/cohorts/{cohort_id}/complexity-score",
    response_model=CohortComplexityScoreResponse,
)
async def get_cohort_complexity_score(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get the complexity score for a cohort.
    """
    result = await db.execute(
        select(CohortComplexityScore).where(
            CohortComplexityScore.cohort_id == cohort_id
        )
    )
    score = result.scalar_one_or_none()
    if not score:
        raise HTTPException(status_code=404, detail="Complexity score not found")

    return score


@router.put(
    "/cohorts/{cohort_id}/complexity-score",
    response_model=CohortComplexityScoreResponse,
)
async def update_cohort_complexity_score(
    cohort_id: uuid.UUID,
    score_data: CohortComplexityScoreUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update the complexity score for a cohort.
    """
    result = await db.execute(
        select(CohortComplexityScore).where(
            CohortComplexityScore.cohort_id == cohort_id
        )
    )
    score = result.scalar_one_or_none()
    if not score:
        raise HTTPException(status_code=404, detail="Complexity score not found")

    # Get member_id from auth user for reviewer tracking
    member_result = await db.execute(
        select(Member.id).where(Member.auth_id == current_user.user_id)
    )
    member_id = member_result.scalar_one_or_none()

    # Update category if provided
    if score_data.category is not None:
        score.category = score_data.category

    # Update dimensions if provided
    if score_data.dimension_1 is not None:
        score.dimension_1_score = score_data.dimension_1.score
        score.dimension_1_rationale = score_data.dimension_1.rationale
    if score_data.dimension_2 is not None:
        score.dimension_2_score = score_data.dimension_2.score
        score.dimension_2_rationale = score_data.dimension_2.rationale
    if score_data.dimension_3 is not None:
        score.dimension_3_score = score_data.dimension_3.score
        score.dimension_3_rationale = score_data.dimension_3.rationale
    if score_data.dimension_4 is not None:
        score.dimension_4_score = score_data.dimension_4.score
        score.dimension_4_rationale = score_data.dimension_4.rationale
    if score_data.dimension_5 is not None:
        score.dimension_5_score = score_data.dimension_5.score
        score.dimension_5_rationale = score_data.dimension_5.rationale
    if score_data.dimension_6 is not None:
        score.dimension_6_score = score_data.dimension_6.score
        score.dimension_6_rationale = score_data.dimension_6.rationale
    if score_data.dimension_7 is not None:
        score.dimension_7_score = score_data.dimension_7.score
        score.dimension_7_rationale = score_data.dimension_7.rationale

    # Recalculate totals
    dimension_scores = [
        score.dimension_1_score,
        score.dimension_2_score,
        score.dimension_3_score,
        score.dimension_4_score,
        score.dimension_5_score,
        score.dimension_6_score,
        score.dimension_7_score,
    ]

    try:
        calc_result = calculate_complexity_score(score.category, dimension_scores)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    score.total_score = calc_result["total_score"]
    score.required_coach_grade = calc_result["required_coach_grade"]
    score.pay_band_min = calc_result["pay_band_min"]
    score.pay_band_max = calc_result["pay_band_max"]
    score.reviewed_by_id = member_id
    score.reviewed_at = utc_now()

    # Update cohort with required grade
    result = await db.execute(select(Cohort).where(Cohort.id == cohort_id))
    cohort = result.scalar_one_or_none()
    if cohort:
        cohort.required_coach_grade = calc_result["required_coach_grade"]

    await db.commit()
    await db.refresh(score)

    return score


@router.delete("/cohorts/{cohort_id}/complexity-score")
async def delete_cohort_complexity_score(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete the complexity score for a cohort.
    """
    result = await db.execute(
        select(CohortComplexityScore).where(
            CohortComplexityScore.cohort_id == cohort_id
        )
    )
    score = result.scalar_one_or_none()
    if not score:
        raise HTTPException(status_code=404, detail="Complexity score not found")

    await db.delete(score)

    # Clear required grade from cohort
    result = await db.execute(select(Cohort).where(Cohort.id == cohort_id))
    cohort = result.scalar_one_or_none()
    if cohort:
        cohort.required_coach_grade = None

    await db.commit()

    return {"message": "Complexity score deleted"}


@router.post("/cohorts/{cohort_id}/complexity-score/review")
async def mark_complexity_score_reviewed(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Mark a complexity score as reviewed (for audit purposes).
    """
    result = await db.execute(
        select(CohortComplexityScore).where(
            CohortComplexityScore.cohort_id == cohort_id
        )
    )
    score = result.scalar_one_or_none()
    if not score:
        raise HTTPException(status_code=404, detail="Complexity score not found")

    # Get member_id from auth user
    member_result = await db.execute(
        select(Member.id).where(Member.auth_id == current_user.user_id)
    )
    member_id = member_result.scalar_one_or_none()

    score.reviewed_by_id = member_id
    score.reviewed_at = utc_now()

    await db.commit()

    return {"message": "Complexity score marked as reviewed"}


@router.get(
    "/cohorts/{cohort_id}/eligible-coaches", response_model=List[EligibleCoachResponse]
)
async def get_eligible_coaches_for_cohort(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get coaches eligible for a cohort based on grade requirements.
    Returns coaches who meet or exceed the required grade.
    """
    # Get cohort with complexity score
    result = await db.execute(
        select(Cohort)
        .options(selectinload(Cohort.complexity_score))
        .where(Cohort.id == cohort_id)
    )
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    if not cohort.required_coach_grade:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cohort has no complexity score. Score the cohort first.",
        )

    required_grade = cohort.required_coach_grade

    # Query coaches with grades that meet or exceed requirement
    # Note: This queries the coach_profiles table which stores grades
    # The grade columns are: learn_to_swim_grade, special_populations_grade, etc.
    # For now, we'll query based on a general coach grade
    # In production, this should match the category-specific grade

    # Get the category from complexity score
    category = None
    if cohort.complexity_score:
        category = cohort.complexity_score.category

    # Map category to grade column name
    grade_column_map = {
        ProgramCategory.LEARN_TO_SWIM: "learn_to_swim_grade",
        ProgramCategory.SPECIAL_POPULATIONS: "special_populations_grade",
        ProgramCategory.INSTITUTIONAL: "institutional_grade",
        ProgramCategory.COMPETITIVE_ELITE: "competitive_elite_grade",
        ProgramCategory.CERTIFICATIONS: "certifications_grade",
        ProgramCategory.SPECIALIZED_DISCIPLINES: "specialized_disciplines_grade",
        ProgramCategory.ADJACENT_SERVICES: "adjacent_services_grade",
    }

    grade_column = grade_column_map.get(category, "learn_to_swim_grade")

    # Build the eligible grades list
    grade_order = [CoachGrade.GRADE_1, CoachGrade.GRADE_2, CoachGrade.GRADE_3]
    required_level = grade_order.index(required_grade)
    eligible_grades = [g.value for g in grade_order[required_level:]]

    # Query eligible coaches
    query = text(
        f"""
        SELECT
            cp.member_id,
            m.first_name || ' ' || m.last_name as name,
            m.email,
            cp.{grade_column} as grade,
            cp.total_coaching_hours,
            cp.average_feedback_rating
        FROM coach_profiles cp
        JOIN members m ON cp.member_id = m.id
        WHERE cp.status = 'active'
        AND cp.{grade_column} IN :eligible_grades
        ORDER BY
            CASE cp.{grade_column}
                WHEN 'grade_3' THEN 1
                WHEN 'grade_2' THEN 2
                WHEN 'grade_1' THEN 3
            END,
            cp.average_feedback_rating DESC NULLS LAST
    """
    )

    result = await db.execute(query, {"eligible_grades": tuple(eligible_grades)})
    rows = result.fetchall()

    return [
        EligibleCoachResponse(
            member_id=row.member_id,
            name=row.name or "Unknown",
            email=row.email,
            grade=CoachGrade(row.grade) if row.grade else CoachGrade.GRADE_1,
            total_coaching_hours=row.total_coaching_hours,
            average_feedback_rating=row.average_feedback_rating,
        )
        for row in rows
    ]
