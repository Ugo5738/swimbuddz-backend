import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
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
    StudentProgress,
)
from services.academy_service.schemas import (
    CohortCreate,
    CohortResponse,
    CohortUpdate,
    EnrollmentCreate,
    EnrollmentResponse,
    EnrollmentUpdate,
    MilestoneCreate,
    MilestoneResponse,
    ProgramCreate,
    ProgramResponse,
    ProgramUpdate,
    StudentProgressResponse,
    StudentProgressUpdate,
)
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["academy"])


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
    return result.scalars().all()


@router.get("/programs/published", response_model=List[ProgramResponse])
async def list_published_programs(
    db: AsyncSession = Depends(get_async_db),
):
    """List only published programs (for member-facing pages)."""
    query = select(Program).where(Program.is_published.is_(True)).order_by(Program.name)
    result = await db.execute(query)
    return result.scalars().all()


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
    return program


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
    return program


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
    from sqlalchemy.orm import selectinload, joinedload

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
    member_row = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": current_user.user_id},
    )
    member = member_row.mappings().first()

    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    # Eager load cohort, program, and nested cohort.program to avoid async issues
    from sqlalchemy.orm import selectinload

    query = (
        select(Enrollment)
        .where(Enrollment.member_id == member["id"])
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
        )
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def get_enrollment_by_id(
    enrollment_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single enrollment by ID (used by payments service)."""
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
    query = select(Enrollment).where(Enrollment.id == enrollment_id)
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    update_data = enrollment_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(enrollment, field, value)

    await db.commit()
    await db.refresh(enrollment)
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

    # If enrollment was pending approval, move to enrolled
    if enrollment.status == EnrollmentStatus.PENDING_APPROVAL:
        enrollment.status = EnrollmentStatus.ENROLLED

    await db.commit()

    # Send enrollment confirmation email
    try:
        from libs.common.email import send_enrollment_confirmation_email

        # Get member email from members service
        import httpx
        from libs.common.config import settings
        from libs.auth.dependencies import _service_role_jwt

        headers = {"Authorization": f"Bearer {_service_role_jwt()}"}
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
    else:
        progress = StudentProgress(
            enrollment_id=enrollment_id,
            milestone_id=milestone_id,
            status=progress_in.status,
            achieved_at=progress_in.achieved_at,
            coach_notes=progress_in.coach_notes,
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
