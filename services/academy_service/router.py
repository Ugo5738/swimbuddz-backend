import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.academy_service.models import (
    Program, Cohort, Enrollment, Milestone, StudentProgress,
    EnrollmentStatus, PaymentStatus, ProgressStatus
)
from services.academy_service.schemas import (
    ProgramCreate, ProgramResponse, ProgramUpdate,
    CohortCreate, CohortResponse, CohortUpdate,
    EnrollmentCreate, EnrollmentResponse, EnrollmentUpdate,
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


@router.get("/my-enrollments", response_model=List[EnrollmentResponse])
async def get_my_enrollments(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    # We need to find the member_id associated with this auth_user
    # This is a bit tricky if we don't have direct access to Member table here easily
    # But we can assume the frontend passes the member_id or we query it.
    # Ideally, we should query the Member service or have a shared library.
    # For now, let's assume we query the Member table directly if we can import it, 
    # OR we rely on the fact that we might store member_id in the token later.
    # But looking at attendance_service, it queries Member table.
    
    from services.members_service.models import Member
    query = select(Member).where(Member.auth_id == current_user.user_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
        
    query = select(Enrollment).where(Enrollment.member_id == member.id)
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
