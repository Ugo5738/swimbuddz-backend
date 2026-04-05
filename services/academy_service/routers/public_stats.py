"""Public (no-auth) academy statistics endpoint.

Surfaces lightweight aggregate counts for the public academy landing
page (open cohorts, total seats, graduates, completion rate).
Every field is non-PII and safe for unauthenticated access.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    CohortStatus,
    Enrollment,
    EnrollmentStatus,
)

router = APIRouter(tags=["academy"])


class AcademyPublicStats(BaseModel):
    """Public aggregate stats for the academy landing page."""

    cohorts_enrolling: int
    cohorts_active: int
    total_seats_open: int
    graduates_all_time: int
    graduates_last_90_days: int
    # Simple completion rate: graduated / (graduated + dropped).
    # Returns None when denominator is 0 (no signal yet).
    completion_rate: float | None


@router.get("/stats/public", response_model=AcademyPublicStats)
async def get_public_academy_stats(
    db: AsyncSession = Depends(get_async_db),
):
    """
    Return lightweight, non-PII academy statistics for public display.
    Single aggregated query per metric; safe to call from unauthenticated
    landing pages.
    """
    from datetime import timedelta

    from libs.common.datetime_utils import utc_now

    now = utc_now()
    ninety_days_ago = now - timedelta(days=90)

    # Cohorts currently enrolling (open and starting in the future)
    enrolling_q = select(func.count(Cohort.id)).where(
        Cohort.status == CohortStatus.OPEN,
        Cohort.start_date > now,
    )
    cohorts_enrolling = (await db.execute(enrolling_q)).scalar() or 0

    # Cohorts currently active
    active_q = select(func.count(Cohort.id)).where(Cohort.status == CohortStatus.ACTIVE)
    cohorts_active = (await db.execute(active_q)).scalar() or 0

    # Total seats across open, future cohorts.
    # We use capacity as the seat total (seats-remaining would require
    # per-cohort enrollment counts).
    seats_q = select(func.coalesce(func.sum(Cohort.capacity), 0)).where(
        Cohort.status == CohortStatus.OPEN,
        Cohort.start_date > now,
    )
    total_seats_open = (await db.execute(seats_q)).scalar() or 0

    # Graduates all-time
    grad_all_q = select(func.count(Enrollment.id)).where(
        Enrollment.status == EnrollmentStatus.GRADUATED
    )
    graduates_all_time = (await db.execute(grad_all_q)).scalar() or 0

    # Graduates in the last 90 days
    grad_recent_q = select(func.count(Enrollment.id)).where(
        Enrollment.status == EnrollmentStatus.GRADUATED,
        Enrollment.updated_at >= ninety_days_ago,
    )
    graduates_last_90_days = (await db.execute(grad_recent_q)).scalar() or 0

    # Completion rate: graduated / (graduated + dropped) across all-time
    dropped_q = select(func.count(Enrollment.id)).where(
        Enrollment.status == EnrollmentStatus.DROPPED
    )
    dropped = (await db.execute(dropped_q)).scalar() or 0

    denominator = graduates_all_time + dropped
    completion_rate: float | None = None
    if denominator > 0:
        completion_rate = round(graduates_all_time / denominator, 3)

    return AcademyPublicStats(
        cohorts_enrolling=int(cohorts_enrolling),
        cohorts_active=int(cohorts_active),
        total_seats_open=int(total_seats_open),
        graduates_all_time=int(graduates_all_time),
        graduates_last_90_days=int(graduates_last_90_days),
        completion_rate=completion_rate,
    )
