"""Coach-facing application + onboarding endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.config import AsyncSessionLocal
from libs.common.datetime_utils import utc_now
from services.members_service.models import CoachProfile, Member
from services.members_service.schemas import (
    CoachApplicationCreate,
    CoachApplicationResponse,
    CoachApplicationStatusResponse,
    CoachOnboardingUpdate,
    CoachPreferencesUpdate,
    CoachProfileUpdate,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ._shared import _build_coach_response, _ensure_wallet_exists

router = APIRouter()


@router.post("/apply", response_model=CoachApplicationResponse)
async def apply_as_coach(
    data: CoachApplicationCreate,
    current_user: AuthUser = Depends(get_current_user),
):
    """
    Submit a coach application.

    If user already has a Member record, creates CoachProfile linked to it.
    If user doesn't have a Member record, creates both.
    """
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Check if member exists
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if member and member.coach_profile:
            # Already has a coach profile
            if member.coach_profile.status in ["pending_review", "approved", "active"]:
                raise HTTPException(
                    status_code=400,
                    detail="You already have an active or pending coach application",
                )
            # Allow resubmission if rejected or draft
            coach_profile = member.coach_profile
        else:
            if not member:
                # Create new member record for coach-only account
                email = current_user.email
                if not email:
                    raise HTTPException(status_code=400, detail="Email required")

                member = Member(
                    auth_id=auth_id,
                    email=email,
                    first_name=data.first_name or email.split("@")[0],
                    last_name=data.last_name or "",
                    roles=["coach"],  # Coach-only, no member entitlements yet
                    registration_complete=False,
                )
                session.add(member)
                await session.flush()

            # Create new coach profile
            coach_profile = CoachProfile(member_id=member.id)
            session.add(coach_profile)

        # Update coach profile with application data
        coach_profile.display_name = data.display_name
        coach_profile.short_bio = data.short_bio
        coach_profile.full_bio = data.full_bio
        coach_profile.coaching_years = data.coaching_years
        coach_profile.coaching_experience_summary = data.coaching_experience_summary
        coach_profile.coaching_specialties = data.coaching_specialties or []
        coach_profile.certifications = data.certifications or []
        coach_profile.other_certifications_note = data.other_certifications_note
        coach_profile.levels_taught = data.levels_taught
        coach_profile.age_groups_taught = data.age_groups_taught
        coach_profile.languages_spoken = data.languages_spoken
        coach_profile.coaching_portfolio_link = data.coaching_portfolio_link
        coach_profile.has_cpr_training = data.has_cpr_training
        coach_profile.cpr_expiry_date = data.cpr_expiry_date
        if data.coaching_document_link:
            coach_profile.coaching_document_link = data.coaching_document_link
            coach_profile.coaching_document_file_name = (
                data.coaching_document_file_name or data.coaching_document_link
            )

        # Set application status
        coach_profile.status = "pending_review"
        coach_profile.application_submitted_at = utc_now()
        coach_profile.rejection_reason = None  # Clear any previous rejection

        # Add coach role if not already present
        if "coach" not in (member.roles or []):
            member.roles = list(set((member.roles or []) + ["coach"]))

        await session.commit()

        if member.auth_id:
            await _ensure_wallet_exists(str(member.id), member.auth_id)

        await session.refresh(member)
        await session.refresh(coach_profile)

        return await _build_coach_response(member, coach_profile)


@router.get("/me", response_model=CoachApplicationResponse)
async def get_my_coach_profile(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the current user's coach profile."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile
        return await _build_coach_response(member, coach)


@router.get("/application-status", response_model=CoachApplicationStatusResponse)
async def get_application_status(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get just the application status (lightweight check)."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            return CoachApplicationStatusResponse(
                status="none",
                can_access_dashboard=False,
            )

        coach = member.coach_profile
        can_access = coach.status in ["approved", "active"]

        return CoachApplicationStatusResponse(
            status=coach.status,
            application_submitted_at=coach.application_submitted_at,
            application_reviewed_at=coach.application_reviewed_at,
            rejection_reason=coach.rejection_reason,
            can_access_dashboard=can_access,
        )


@router.patch("/me", response_model=CoachApplicationResponse)
async def update_my_coach_profile(
    data: CoachProfileUpdate,
    current_user: AuthUser = Depends(get_current_user),
):
    """Update the current user's coach profile."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile

        # Update fields that were provided
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if hasattr(coach, field):
                setattr(coach, field, value)

        await session.commit()
        await session.refresh(coach)

        return await _build_coach_response(member, coach)


@router.post("/me/preferences", response_model=CoachApplicationResponse)
async def update_my_coach_preferences(
    data: CoachPreferencesUpdate,
    current_user: AuthUser = Depends(get_current_user),
):
    """Update the current coach's preferences (post-onboarding)."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile

        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if hasattr(coach, field):
                setattr(coach, field, value)

        await session.commit()
        await session.refresh(coach)

        return await _build_coach_response(member, coach)


@router.post("/me/onboarding", response_model=CoachApplicationResponse)
async def complete_coach_onboarding(
    data: CoachOnboardingUpdate,
    current_user: AuthUser = Depends(get_current_user),
):
    """Complete coach onboarding (for approved coaches)."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile

        if coach.status not in ["approved", "active"]:
            raise HTTPException(
                status_code=400,
                detail="Coach must be approved before completing onboarding",
            )

        # Update onboarding fields
        if data.availability_calendar is not None:
            coach.availability_calendar = data.availability_calendar
        if data.pools_supported is not None:
            coach.pools_supported = data.pools_supported
        if data.can_travel_between_pools is not None:
            coach.can_travel_between_pools = data.can_travel_between_pools
        if data.travel_radius_km is not None:
            coach.travel_radius_km = data.travel_radius_km
        if data.accepts_one_on_one is not None:
            coach.accepts_one_on_one = data.accepts_one_on_one
        if data.accepts_group_cohorts is not None:
            coach.accepts_group_cohorts = data.accepts_group_cohorts
        if data.max_swimmers_per_session is not None:
            coach.max_swimmers_per_session = data.max_swimmers_per_session
        if data.max_cohorts_at_once is not None:
            coach.max_cohorts_at_once = data.max_cohorts_at_once
        if data.preferred_cohort_types is not None:
            coach.preferred_cohort_types = data.preferred_cohort_types
        if data.coach_profile_photo_media_id is not None:
            coach.coach_profile_photo_media_id = data.coach_profile_photo_media_id
        if data.currency is not None:
            coach.currency = data.currency
        if data.one_to_one_rate_per_hour is not None:
            coach.one_to_one_rate_per_hour = data.one_to_one_rate_per_hour
        if data.group_session_rate_per_hour is not None:
            coach.group_session_rate_per_hour = data.group_session_rate_per_hour
        if data.academy_cohort_stipend is not None:
            coach.academy_cohort_stipend = data.academy_cohort_stipend
        if data.show_in_directory is not None:
            coach.show_in_directory = data.show_in_directory

        # Set status to active after onboarding
        coach.status = "active"

        await session.commit()
        await session.refresh(coach)

        return await _build_coach_response(member, coach)
