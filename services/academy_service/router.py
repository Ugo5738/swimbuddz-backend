import uuid
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import _service_role_jwt, get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.email import send_enrollment_confirmation_email
from libs.common.media_utils import resolve_media_url, resolve_media_urls
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    CohortStatus,
    Enrollment,
    EnrollmentStatus,
    Milestone,
    PaymentStatus,
    Program,
    ProgramLevel,
    ProgressStatus,
    StudentProgress,
)
from services.academy_service.schemas import (
    CohortCreate,
    CohortResponse,
    CohortUpdate,
    EnrollmentCreate,
    EnrollmentResponse,
    EnrollmentUpdate,
    MemberMilestoneClaimRequest,
    MilestoneCreate,
    MilestoneResponse,
    NextSessionInfo,
    OnboardingResponse,
    ProgramCreate,
    ProgramResponse,
    ProgramUpdate,
    StudentProgressResponse,
    StudentProgressUpdate,
)
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(tags=["academy"])


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

    cohort = Cohort(**cohort_in.model_dump())
    db.add(cohort)
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

    query = select(Cohort).where(Cohort.id == cohort_id)
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


@router.get("/cohorts/{cohort_id}/students", response_model=List[EnrollmentResponse])
async def list_cohort_students(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all students enrolled in a cohort with their progress."""
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
        .order_by(Milestone.name)
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

        # Validation for mid-entry if cohort is selected
        if cohort.status != CohortStatus.OPEN:
            # Fetch program for level check
            program_query = select(Program).where(Program.id == cohort.program_id)
            program_result = await db.execute(program_query)
            program = program_result.scalar_one_or_none()

            if program and program.level in [
                ProgramLevel.BEGINNER_1,
                ProgramLevel.BEGINNER_2,
                ProgramLevel.INTERMEDIATE,
            ]:
                if cohort.status == CohortStatus.ACTIVE:
                    raise HTTPException(
                        status_code=400,
                        detail="This cohort has already started. Please join a waitlist or next available cohort.",
                    )

            if cohort.status != CohortStatus.OPEN:
                # Allow joining waitlist? For now, block unless OPEN.
                # Actually, if status is PENDING_APPROVAL, maybe we allow it?
                # Let's say if it's not OPEN, we warn.
                # But the requirement says "Strict mid-entry rules". So block.
                pass

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

    # 4. Create Enrollment Request
    # Status is PENDING_APPROVAL by default for everyone now (as per new workflow)
    # Payment status handles the financial part separate from Admission.
    enrollment = Enrollment(
        program_id=program_id,
        cohort_id=cohort_id,  # Can be None
        member_id=member["id"],
        member_auth_id=current_user.user_id,  # For decoupled ownership verification
        status=EnrollmentStatus.PENDING_APPROVAL,
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

    # Find next session (query sessions service)
    # For now, we'll use cohort start_date as placeholder
    next_session = NextSessionInfo(
        date=cohort.start_date if cohort.start_date > utc_now() else None,
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

                    await send_enrollment_confirmation_email(
                        to_email=member_email,
                        member_name=member_name,
                        program_name=program_name,
                        cohort_name=cohort_name,
                        start_date=start_date,
                    )
    except Exception as e:
        # Log but don't fail the request if email fails
        import logging

        logging.getLogger(__name__).warning(
            f"Failed to send enrollment confirmation email: {e}"
        )

    # Re-fetch with relationships for response
    result = await db.execute(query)
    return result.scalar_one()


# --- Progress ---


@router.post("/progress", response_model=StudentProgressResponse)
async def update_student_progress(
    progress_in: StudentProgressUpdate,
    enrollment_id: uuid.UUID,
    milestone_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),  # Coach/Admin
    db: AsyncSession = Depends(get_async_db),
):
    """Update or create student progress (Coach/Admin only)."""

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
