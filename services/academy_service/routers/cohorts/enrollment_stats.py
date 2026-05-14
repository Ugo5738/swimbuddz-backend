"""Cohort enrollment-stats + students-listing endpoints."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_coach, require_coach_for_cohort
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_id
from libs.db.session import get_async_db
from services.academy_service.models import Cohort, Enrollment, EnrollmentStatus
from services.academy_service.routers._shared import (
    _sync_installment_state_for_enrollment,
)
from services.academy_service.schemas import EnrollmentResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

router = APIRouter(tags=["academy"])


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

    # Eager load progress records, cohort, and program
    query = (
        select(Enrollment)
        .where(Enrollment.cohort_id == cohort_id)
        .options(
            selectinload(Enrollment.progress_records),
            joinedload(Enrollment.cohort).joinedload(Cohort.program),
            joinedload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
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
