"""Enrollment lookups + analytics scoped to a single cohort."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    Enrollment,
    EnrollmentStatus,
    Milestone,
    StudentProgress,
)
from services.academy_service.routers._shared import (
    _sync_installment_state_for_enrollment,
)
from services.academy_service.schemas import EnrollmentResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter()


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
            selectinload(Enrollment.progress_records),
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
