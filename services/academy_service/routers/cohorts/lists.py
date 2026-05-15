"""Cohort listing endpoints.

These are registered BEFORE the CRUD sub-router so that `/cohorts/open`,
`/cohorts/enrollable`, `/cohorts/by-coach/{id}`, and `/cohorts/coach/me`
take priority over the generic `/cohorts/{cohort_id}` catch-all.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    CohortStatus,
    Enrollment,
    EnrollmentStatus,
    Program,
)
from services.academy_service.routers._shared import _is_mid_entry_open_now
from services.academy_service.schemas import CohortResponse

router = APIRouter()


@router.get("/cohorts", response_model=List[CohortResponse])
async def list_cohorts(
    program_id: uuid.UUID = None,
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Cohort).order_by(Cohort.start_date.desc())
    if program_id:
        query = query.where(Cohort.program_id == program_id)
    query = query.options(selectinload(Cohort.program))

    result = await db.execute(query)
    return result.scalars().all()


async def _annotate_enrollment_counts(db: AsyncSession, cohorts: List[Cohort]) -> None:
    """Stamp each cohort with `enrolled_count` and `is_full` so the API response
    can drive waitlist UX without extra client round trips.

    A seat is "occupied" when an enrollment is ENROLLED or PENDING_APPROVAL —
    same rule used in enrollments.py when deciding whether a new enrolment
    should be auto-waitlisted.
    """
    if not cohorts:
        return
    cohort_ids = [c.id for c in cohorts]
    count_rows = await db.execute(
        select(Enrollment.cohort_id, func.count())
        .where(Enrollment.cohort_id.in_(cohort_ids))
        .where(
            Enrollment.status.in_(
                [EnrollmentStatus.ENROLLED, EnrollmentStatus.PENDING_APPROVAL]
            )
        )
        .group_by(Enrollment.cohort_id)
    )
    counts = {cohort_id: count for cohort_id, count in count_rows.all()}
    for cohort in cohorts:
        enrolled_count = counts.get(cohort.id, 0)
        cohort.enrolled_count = enrolled_count
        cohort.is_full = bool(cohort.capacity and enrolled_count >= cohort.capacity)


async def _list_enrollable_cohorts(
    db: AsyncSession, program_id: uuid.UUID | None = None
) -> List[Cohort]:
    """Return cohorts a member can enroll in right now — OPEN cohorts plus
    ACTIVE cohorts where mid-entry is enabled and still within the cutoff.
    Annotates each with enrollment counts.
    """
    now = utc_now()

    query = (
        select(Cohort)
        .join(Program, Cohort.program_id == Program.id)
        .where(Program.is_published.is_(True))
        .where(
            or_(
                Cohort.status == CohortStatus.OPEN,
                and_(
                    Cohort.status == CohortStatus.ACTIVE,
                    Cohort.allow_mid_entry.is_(True),
                ),
            )
        )
        .options(selectinload(Cohort.program))
        .order_by(Cohort.start_date.asc())
    )

    if program_id:
        query = query.where(Cohort.program_id == program_id)

    result = await db.execute(query)
    cohorts = [
        cohort
        for cohort in result.scalars().all()
        if cohort.status == CohortStatus.OPEN or _is_mid_entry_open_now(cohort, now)
    ]
    await _annotate_enrollment_counts(db, cohorts)
    return cohorts


@router.get("/cohorts/open", response_model=List[CohortResponse])
async def list_open_cohorts(
    db: AsyncSession = Depends(get_async_db),
):
    """List cohorts members can enroll in — OPEN cohorts plus ACTIVE cohorts
    with mid-entry still open. Only from published programs.

    Previously returned just OPEN status; broadened so the public academy page
    surfaces in-progress cohorts that still accept mid-entry. Callers that
    need only-not-yet-started cohorts should filter by status client-side.
    """
    return await _list_enrollable_cohorts(db)


@router.get("/cohorts/enrollable", response_model=List[CohortResponse])
async def list_enrollable_cohorts(
    program_id: uuid.UUID = None,
    db: AsyncSession = Depends(get_async_db),
):
    """List cohorts members can enroll in right now.

    Includes:
    - OPEN cohorts (published programs)
    - ACTIVE cohorts where mid-entry is enabled and still within cutoff week

    Identical semantics to /cohorts/open; this endpoint additionally accepts a
    program_id filter.
    """
    return await _list_enrollable_cohorts(db, program_id=program_id)


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

    # 1. Resolve Member ID via members-service
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")

    # 2. Query Cohorts
    query = (
        select(Cohort)
        .where(Cohort.coach_id == uuid.UUID(member["id"]))
        .options(selectinload(Cohort.program))
        .order_by(Cohort.start_date.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()
