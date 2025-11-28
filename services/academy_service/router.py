import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.academy_service.models import (
    Program,
    Cohort,
    Enrollment,
    Milestone,
    StudentProgress,
    EnrollmentStatus,
    PaymentStatus,
    ProgressStatus,
    CohortStatus,
    Member,
)
from services.academy_service.schemas import (
    ProgramCreate, ProgramResponse, ProgramUpdate,
    CohortCreate, CohortResponse, CohortUpdate,
    EnrollmentCreate, EnrollmentResponse, EnrollmentUpdate, EnrollmentWithStudent,
    MilestoneCreate, MilestoneResponse, MilestoneUpdate,
    StudentProgressResponse, StudentProgressUpdate
)

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


@router.get("/programs", response_model=List[ProgramResponse])
async def list_programs(
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Program).order_by(Program.name)
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
    cohort = Cohort(**cohort_in.model_dump())
    db.add(cohort)
    await db.commit()
    await db.refresh(cohort)
    return cohort


@router.put("/cohorts/{cohort_id}", response_model=CohortResponse)
async def update_cohort(
    cohort_id: uuid.UUID,
    cohort_in: CohortUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Cohort).where(Cohort.id == cohort_id)
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()
    
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
        
    update_data = cohort_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(cohort, field, value)
        
    await db.commit()
    await db.refresh(cohort)
    return cohort


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
    query = select(Cohort).order_by(Cohort.start_date.desc())
    if program_id:
        query = query.where(Cohort.program_id == program_id)
    
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/cohorts/open", response_model=List[CohortResponse])
async def list_open_cohorts(
    db: AsyncSession = Depends(get_async_db),
):
    """List all cohorts with status OPEN."""
    query = select(Cohort).where(Cohort.status == CohortStatus.OPEN).order_by(Cohort.start_date.asc())
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/cohorts/{cohort_id}", response_model=CohortResponse)
async def get_cohort(
    cohort_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Cohort).where(Cohort.id == cohort_id)
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    return cohort


@router.get("/cohorts/{cohort_id}/students", response_model=List[EnrollmentWithStudent])
async def list_cohort_students(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all students enrolled in a cohort with their progress."""
    # Eager load member and progress_records
    from sqlalchemy.orm import selectinload
    query = (
        select(Enrollment)
        .where(Enrollment.cohort_id == cohort_id)
        .options(
            selectinload(Enrollment.member),
            selectinload(Enrollment.progress_records)
        )
    )
    result = await db.execute(query)
    return result.scalars().all()


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
    query = select(Milestone).where(Milestone.program_id == program_id).order_by(Milestone.name)
    result = await db.execute(query)
    return result.scalars().all()


# --- Enrollments ---

@router.post("/enrollments", response_model=EnrollmentResponse)
async def enroll_student(
    enrollment_in: EnrollmentCreate,
    current_user: AuthUser = Depends(require_admin), # Admin can enroll anyone
    db: AsyncSession = Depends(get_async_db),
):
    # Check if already enrolled
    query = select(Enrollment).where(
        Enrollment.cohort_id == enrollment_in.cohort_id,
        Enrollment.member_id == enrollment_in.member_id
    )
    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(status_code=400, detail="Member already enrolled in this cohort")
        
    enrollment = Enrollment(**enrollment_in.model_dump())
    db.add(enrollment)
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


@router.get("/enrollments", response_model=List[EnrollmentResponse])
async def list_enrollments(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all enrollments (admin only)."""
    from sqlalchemy.orm import selectinload
    query = (
        select(Enrollment)
        .options(
            selectinload(Enrollment.cohort),
            selectinload(Enrollment.member)
        )
        .order_by(Enrollment.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/enrollments/me", response_model=EnrollmentResponse)
async def self_enroll(
    request_data: dict,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Allow a member to enroll themselves in a cohort."""
    cohort_id = request_data.get("cohort_id")
    if not cohort_id:
        raise HTTPException(status_code=422, detail="cohort_id is required in request body")
    
    # 1. Get Member ID
    query = select(Member).where(Member.auth_id == current_user.user_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found. Please complete registration.")

    # 2. Check Cohort Status
    cohort_query = select(Cohort).where(Cohort.id == cohort_id)
    cohort_result = await db.execute(cohort_query)
    cohort = cohort_result.scalar_one_or_none()
    
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    
    if cohort.status != CohortStatus.OPEN:
        raise HTTPException(status_code=400, detail="This cohort is not open for enrollment")

    # 3. Check Existing Enrollment
    query = select(Enrollment).where(
        Enrollment.cohort_id == cohort_id,
        Enrollment.member_id == member.id
    )
    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(status_code=400, detail="You are already enrolled in this cohort")
        
    # 4. Create Enrollment (Auto-PAID for now as per plan)
    enrollment = Enrollment(
        cohort_id=cohort_id,
        member_id=member.id,
        status=EnrollmentStatus.ENROLLED,
        payment_status=PaymentStatus.PAID 
    )
    db.add(enrollment)
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


@router.get("/my-enrollments", response_model=List[EnrollmentResponse])
async def get_my_enrollments(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Member).where(Member.auth_id == current_user.user_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
        
    # Eager load cohort details
    from sqlalchemy.orm import selectinload
    query = (
        select(Enrollment)
        .where(Enrollment.member_id == member.id)
        .options(selectinload(Enrollment.cohort))
    )
    result = await db.execute(query)
    return result.scalars().all()


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


# --- Progress ---

@router.post("/progress", response_model=StudentProgressResponse)
async def update_student_progress(
    progress_in: StudentProgressUpdate,
    enrollment_id: uuid.UUID,
    milestone_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin), # Coach/Admin
    db: AsyncSession = Depends(get_async_db),
):
    # Check if record exists
    query = select(StudentProgress).where(
        StudentProgress.enrollment_id == enrollment_id,
        StudentProgress.milestone_id == milestone_id
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
            coach_notes=progress_in.coach_notes
        )
        db.add(progress)
        
    await db.commit()
    await db.refresh(progress)
    return progress


@router.get("/enrollments/{enrollment_id}/progress", response_model=List[StudentProgressResponse])
async def get_student_progress(
    enrollment_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    query = select(StudentProgress).where(StudentProgress.enrollment_id == enrollment_id)
    result = await db.execute(query)
    return result.scalars().all()
