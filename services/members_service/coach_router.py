"""Coach-specific API routes for application, onboarding, and profile management."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.db.config import AsyncSessionLocal
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .coach_schemas import (
    AdminApproveCoach,
    AdminCoachApplicationDetail,
    AdminCoachApplicationListItem,
    AdminRejectCoach,
    AdminRequestMoreInfo,
    CoachApplicationCreate,
    CoachApplicationResponse,
    CoachApplicationStatusResponse,
    CoachOnboardingUpdate,
    CoachProfileUpdate,
)
from .models import CoachProfile, Member

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/coaches", tags=["coaches"])
admin_router = APIRouter(prefix="/admin/coaches", tags=["admin-coaches"])


# === Helper Functions ===


async def get_member_by_auth_id(auth_id: str) -> Optional[Member]:
    """Get member by Supabase auth ID."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        return result.scalar_one_or_none()


async def get_coach_profile_by_member_id(member_id: str) -> Optional[CoachProfile]:
    """Get coach profile by member ID."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CoachProfile).where(CoachProfile.member_id == member_id)
        )
        return result.scalar_one_or_none()


# === Coach Application Endpoints ===


@router.post("/apply", response_model=CoachApplicationResponse)
async def apply_as_coach(
    data: CoachApplicationCreate,
    user: dict = Depends(get_current_user),
):
    """
    Submit a coach application.

    If user already has a Member record, creates CoachProfile linked to it.
    If user doesn't have a Member record, creates both.
    """
    auth_id = user.get("sub")
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
                email = user.get("email")
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
            member.coaching_document_link = data.coaching_document_link
            member.coaching_document_file_name = (
                data.coaching_document_file_name or data.coaching_document_link
            )

        # Set application status
        coach_profile.status = "pending_review"
        coach_profile.application_submitted_at = datetime.now(timezone.utc)
        coach_profile.rejection_reason = None  # Clear any previous rejection

        # Add coach role if not already present
        if "coach" not in (member.roles or []):
            member.roles = list(set((member.roles or []) + ["coach"]))

        await session.commit()
        await session.refresh(member)
        await session.refresh(coach_profile)

        return CoachApplicationResponse(
            id=str(coach_profile.id),
            member_id=str(member.id),
            email=member.email,
            first_name=member.first_name,
            last_name=member.last_name,
            display_name=coach_profile.display_name,
            status=coach_profile.status,
            short_bio=coach_profile.short_bio,
            coaching_years=coach_profile.coaching_years or 0,
            coaching_specialties=coach_profile.coaching_specialties or [],
            certifications=coach_profile.certifications or [],
            coaching_document_link=member.coaching_document_link,
            coaching_document_file_name=member.coaching_document_file_name,
            application_submitted_at=coach_profile.application_submitted_at,
            application_reviewed_at=coach_profile.application_reviewed_at,
            rejection_reason=coach_profile.rejection_reason,
            created_at=coach_profile.created_at,
            updated_at=coach_profile.updated_at,
        )


@router.get("/me", response_model=CoachApplicationResponse)
async def get_my_coach_profile(user: dict = Depends(get_current_user)):
    """Get the current user's coach profile."""
    auth_id = user.get("sub")
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
        return CoachApplicationResponse(
            id=str(coach.id),
            member_id=str(member.id),
            email=member.email,
            first_name=member.first_name,
            last_name=member.last_name,
            display_name=coach.display_name,
            status=coach.status,
            short_bio=coach.short_bio,
            coaching_years=coach.coaching_years or 0,
            coaching_specialties=coach.coaching_specialties or [],
            certifications=coach.certifications or [],
            coaching_document_link=member.coaching_document_link,
            coaching_document_file_name=member.coaching_document_file_name,
            application_submitted_at=coach.application_submitted_at,
            application_reviewed_at=coach.application_reviewed_at,
            rejection_reason=coach.rejection_reason,
            created_at=coach.created_at,
            updated_at=coach.updated_at,
        )


@router.get("/application-status", response_model=CoachApplicationStatusResponse)
async def get_application_status(user: dict = Depends(get_current_user)):
    """Get just the application status (lightweight check)."""
    auth_id = user.get("sub")
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
    user: dict = Depends(get_current_user),
):
    """Update the current user's coach profile."""
    auth_id = user.get("sub")
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

        return CoachApplicationResponse(
            id=str(coach.id),
            member_id=str(member.id),
            email=member.email,
            first_name=member.first_name,
            last_name=member.last_name,
            display_name=coach.display_name,
            status=coach.status,
            short_bio=coach.short_bio,
            coaching_years=coach.coaching_years or 0,
            coaching_specialties=coach.coaching_specialties or [],
            certifications=coach.certifications or [],
            application_submitted_at=coach.application_submitted_at,
            application_reviewed_at=coach.application_reviewed_at,
            rejection_reason=coach.rejection_reason,
            created_at=coach.created_at,
            updated_at=coach.updated_at,
        )


@router.post("/me/onboarding", response_model=CoachApplicationResponse)
async def complete_coach_onboarding(
    data: CoachOnboardingUpdate,
    user: dict = Depends(get_current_user),
):
    """Complete coach onboarding (for approved coaches)."""
    auth_id = user.get("sub")
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
        if data.coach_profile_photo_url is not None:
            coach.coach_profile_photo_url = data.coach_profile_photo_url

        # Set status to active after onboarding
        coach.status = "active"

        await session.commit()
        await session.refresh(coach)

        return CoachApplicationResponse(
            id=str(coach.id),
            member_id=str(member.id),
            email=member.email,
            first_name=member.first_name,
            last_name=member.last_name,
            display_name=coach.display_name,
            status=coach.status,
            short_bio=coach.short_bio,
            coaching_years=coach.coaching_years or 0,
            coaching_specialties=coach.coaching_specialties or [],
            certifications=coach.certifications or [],
            application_submitted_at=coach.application_submitted_at,
            application_reviewed_at=coach.application_reviewed_at,
            rejection_reason=coach.rejection_reason,
            created_at=coach.created_at,
            updated_at=coach.updated_at,
        )


# === Admin Coach Review Endpoints ===


@admin_router.get("/applications", response_model=list[AdminCoachApplicationListItem])
async def list_coach_applications(
    status: Optional[str] = None,
    _admin: dict = Depends(require_admin),
):
    """List coach applications (admin only)."""
    async with AsyncSessionLocal() as session:
        query = (
            select(CoachProfile).join(Member).options(selectinload(CoachProfile.member))
        )

        if status:
            query = query.where(CoachProfile.status == status)
        else:
            # Default to pending applications
            query = query.where(
                CoachProfile.status.in_(["pending_review", "more_info_needed"])
            )

        query = query.order_by(CoachProfile.application_submitted_at.desc())

        result = await session.execute(query)
        profiles = result.scalars().all()

        return [
            AdminCoachApplicationListItem(
                id=str(p.id),
                member_id=str(p.member_id),
                email=p.member.email,
                first_name=p.member.first_name,
                last_name=p.member.last_name,
                display_name=p.display_name,
                status=p.status,
                coaching_years=p.coaching_years or 0,
                coaching_specialties=p.coaching_specialties or [],
                certifications=p.certifications or [],
                coaching_document_link=p.member.coaching_document_link,
                coaching_document_file_name=p.member.coaching_document_file_name,
                application_submitted_at=p.application_submitted_at,
                created_at=p.created_at,
            )
            for p in profiles
        ]


@admin_router.get(
    "/applications/{coach_profile_id}", response_model=AdminCoachApplicationDetail
)
async def get_coach_application(
    coach_profile_id: str,
    _admin: dict = Depends(require_admin),
):
    """Get a single coach application for review (admin only)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CoachProfile)
            .options(selectinload(CoachProfile.member))
            .where(CoachProfile.id == coach_profile_id)
        )
        coach = result.scalar_one_or_none()

        if not coach:
            raise HTTPException(status_code=404, detail="Coach application not found")

        return AdminCoachApplicationDetail(
            id=str(coach.id),
            member_id=str(coach.member_id),
            email=coach.member.email,
            first_name=coach.member.first_name,
            last_name=coach.member.last_name,
            phone=coach.member.phone,
            display_name=coach.display_name,
            coach_profile_photo_url=coach.coach_profile_photo_url,
            short_bio=coach.short_bio,
            full_bio=coach.full_bio,
            certifications=coach.certifications or [],
            other_certifications_note=coach.other_certifications_note,
            coaching_years=coach.coaching_years or 0,
            coaching_experience_summary=coach.coaching_experience_summary,
            coaching_specialties=coach.coaching_specialties or [],
            coaching_document_link=coach.member.coaching_document_link,
            coaching_document_file_name=coach.member.coaching_document_file_name,
            levels_taught=coach.levels_taught or [],
            age_groups_taught=coach.age_groups_taught or [],
            languages_spoken=coach.languages_spoken or [],
            coaching_portfolio_link=coach.coaching_portfolio_link,
            has_cpr_training=coach.has_cpr_training,
            cpr_expiry_date=coach.cpr_expiry_date,
            background_check_status=coach.background_check_status,
            background_check_document_url=coach.background_check_document_url,
            status=coach.status,
            application_submitted_at=coach.application_submitted_at,
            application_reviewed_at=coach.application_reviewed_at,
            application_reviewed_by=coach.application_reviewed_by,
            rejection_reason=coach.rejection_reason,
            admin_notes=coach.admin_notes,
            created_at=coach.created_at,
            updated_at=coach.updated_at,
        )


@admin_router.post("/applications/{coach_profile_id}/approve")
async def approve_coach_application(
    coach_profile_id: str,
    data: AdminApproveCoach,
    admin: dict = Depends(require_admin),
):
    """Approve a coach application (admin only)."""
    admin_email = admin.get("email", "admin")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CoachProfile)
            .options(selectinload(CoachProfile.member))
            .where(CoachProfile.id == coach_profile_id)
        )
        coach = result.scalar_one_or_none()

        if not coach:
            raise HTTPException(status_code=404, detail="Coach application not found")

        if coach.status not in ["pending_review", "more_info_needed"]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve application with status: {coach.status}",
            )

        coach.status = "approved"
        coach.application_reviewed_at = datetime.now(timezone.utc)
        coach.application_reviewed_by = admin_email
        coach.rejection_reason = None
        if data.admin_notes:
            coach.admin_notes = data.admin_notes

        await session.commit()

        logger.info(f"Coach application {coach_profile_id} approved by {admin_email}")

        # TODO: Send approval email to coach

        return {"message": "Coach application approved", "status": "approved"}


@admin_router.post("/applications/{coach_profile_id}/reject")
async def reject_coach_application(
    coach_profile_id: str,
    data: AdminRejectCoach,
    admin: dict = Depends(require_admin),
):
    """Reject a coach application (admin only)."""
    admin_email = admin.get("email", "admin")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CoachProfile)
            .options(selectinload(CoachProfile.member))
            .where(CoachProfile.id == coach_profile_id)
        )
        coach = result.scalar_one_or_none()

        if not coach:
            raise HTTPException(status_code=404, detail="Coach application not found")

        if coach.status not in ["pending_review", "more_info_needed"]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reject application with status: {coach.status}",
            )

        coach.status = "rejected"
        coach.application_reviewed_at = datetime.now(timezone.utc)
        coach.application_reviewed_by = admin_email
        coach.rejection_reason = data.rejection_reason
        if data.admin_notes:
            coach.admin_notes = data.admin_notes

        await session.commit()

        logger.info(f"Coach application {coach_profile_id} rejected by {admin_email}")

        # TODO: Send rejection email to coach

        return {"message": "Coach application rejected", "status": "rejected"}


@admin_router.post("/applications/{coach_profile_id}/request-info")
async def request_more_info(
    coach_profile_id: str,
    data: AdminRequestMoreInfo,
    admin: dict = Depends(require_admin),
):
    """Request more information from a coach applicant (admin only)."""
    admin_email = admin.get("email", "admin")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CoachProfile)
            .options(selectinload(CoachProfile.member))
            .where(CoachProfile.id == coach_profile_id)
        )
        coach = result.scalar_one_or_none()

        if not coach:
            raise HTTPException(status_code=404, detail="Coach application not found")

        if coach.status != "pending_review":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot request info for application with status: {coach.status}",
            )

        coach.status = "more_info_needed"
        coach.rejection_reason = data.message  # Store the request message
        if data.admin_notes:
            coach.admin_notes = data.admin_notes

        await session.commit()

        logger.info(
            f"More info requested for coach application {coach_profile_id} by {admin_email}"
        )

        # TODO: Send email to coach requesting more info

        return {"message": "More info requested", "status": "more_info_needed"}
