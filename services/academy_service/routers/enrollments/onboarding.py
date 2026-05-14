"""Enrollment onboarding endpoint (GET /my-enrollments/{id}/onboarding)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.service_client import (
    get_member_by_id,
    get_next_session_for_cohort,
)
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    Enrollment,
    Milestone,
)
from services.academy_service.routers._shared import (
    _sync_installment_state_for_enrollment,
)
from services.academy_service.schemas import NextSessionInfo, OnboardingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(tags=["academy"])


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
