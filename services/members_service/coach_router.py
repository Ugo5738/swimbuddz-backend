"""Coach-specific API routes for application, onboarding, and profile management."""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.email import send_email
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_url
from libs.common.supabase import get_supabase_admin_client
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

logger = get_logger(__name__)

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


def _build_coach_response(
    member: Member, coach: CoachProfile
) -> CoachApplicationResponse:
    """Build CoachApplicationResponse from Member and CoachProfile models."""
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
        other_certifications_note=coach.other_certifications_note,
        levels_taught=coach.levels_taught,
        age_groups_taught=coach.age_groups_taught,
        languages_spoken=coach.languages_spoken,
        coaching_portfolio_link=coach.coaching_portfolio_link,
        has_cpr_training=coach.has_cpr_training,
        cpr_expiry_date=coach.cpr_expiry_date,
        coaching_document_link=coach.coaching_document_link,
        coaching_document_file_name=coach.coaching_document_file_name,
        application_submitted_at=coach.application_submitted_at,
        application_reviewed_at=coach.application_reviewed_at,
        rejection_reason=coach.rejection_reason,
        created_at=coach.created_at,
        updated_at=coach.updated_at,
    )


# === Coach Application Endpoints ===


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
        coach_profile.application_submitted_at = datetime.now(timezone.utc)
        coach_profile.rejection_reason = None  # Clear any previous rejection

        # Add coach role if not already present
        if "coach" not in (member.roles or []):
            member.roles = list(set((member.roles or []) + ["coach"]))

        await session.commit()
        await session.refresh(member)
        await session.refresh(coach_profile)

        return _build_coach_response(member, coach_profile)


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
        return _build_coach_response(member, coach)


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

        return _build_coach_response(member, coach)


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

        return _build_coach_response(member, coach)


# === Admin Coach Review Endpoints ===


@admin_router.get("/applications", response_model=list[AdminCoachApplicationListItem])
async def list_coach_applications(
    application_status: Optional[str] = None,
    _admin: dict = Depends(require_admin),
):
    """List coach applications (admin only)."""
    async with AsyncSessionLocal() as session:
        query = (
            select(CoachProfile).join(Member).options(selectinload(CoachProfile.member))
        )

        if application_status and application_status.lower() != "all":
            query = query.where(CoachProfile.status == application_status)
        elif not application_status:
            # Default (no filter provided) -> Pending applications only
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
                coaching_document_link=p.coaching_document_link,
                coaching_document_file_name=p.coaching_document_file_name,
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

        # Resolve coach profile photo URL from media_id
        coach_photo_url = (
            await resolve_media_url(coach.coach_profile_photo_media_id)
            if coach.coach_profile_photo_media_id
            else None
        )

        return AdminCoachApplicationDetail(
            id=str(coach.id),
            member_id=str(coach.member_id),
            email=coach.member.email,
            first_name=coach.member.first_name,
            last_name=coach.member.last_name,
            phone=coach.member.profile.phone if coach.member.profile else None,
            display_name=coach.display_name,
            coach_profile_photo_url=coach_photo_url,
            short_bio=coach.short_bio,
            full_bio=coach.full_bio,
            certifications=coach.certifications or [],
            other_certifications_note=coach.other_certifications_note,
            coaching_years=coach.coaching_years or 0,
            coaching_experience_summary=coach.coaching_experience_summary,
            coaching_specialties=coach.coaching_specialties or [],
            coaching_document_link=coach.coaching_document_link,
            coaching_document_file_name=coach.coaching_document_file_name,
            levels_taught=coach.levels_taught or [],
            age_groups_taught=coach.age_groups_taught or [],
            languages_spoken=coach.languages_spoken or [],
            coaching_portfolio_link=coach.coaching_portfolio_link,
            has_cpr_training=coach.has_cpr_training,
            cpr_expiry_date=coach.cpr_expiry_date,
            background_check_status=coach.background_check_status,
            background_check_document_url=None,  # TODO: Resolve from coach.background_check_document_media_id
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
    admin: AuthUser = Depends(require_admin),
):
    """Approve a coach application (admin only)."""
    admin_email = admin.email or "admin"

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

        # Add "coach" role to Supabase app_metadata.roles
        # This ensures the JWT includes the coach role for auth checks
        member = coach.member
        if member and member.auth_id:
            try:
                admin_supabase = get_supabase_admin_client()
                # Get current user to preserve existing roles
                current_user = await asyncio.to_thread(
                    admin_supabase.auth.admin.get_user_by_id, member.auth_id
                )
                existing_roles = []
                if current_user and current_user.user:
                    existing_metadata = (
                        getattr(current_user.user, "app_metadata", {}) or {}
                    )
                    existing_roles = existing_metadata.get("roles", [])

                # Add coach role if not already present
                if "coach" not in existing_roles:
                    new_roles = list(set(existing_roles + ["coach"]))
                    await asyncio.to_thread(
                        admin_supabase.auth.admin.update_user_by_id,
                        member.auth_id,
                        {"app_metadata": {"roles": new_roles}},
                    )
                    logger.info(
                        f"Added coach role to Supabase for user {member.auth_id}",
                        extra={"extra_fields": {"roles": new_roles}},
                    )
            except Exception as e:
                # Log but don't fail the approval if Supabase update fails
                # The coach can still be activated later
                logger.warning(
                    f"Could not update Supabase app_metadata for coach: {e}",
                    extra={
                        "extra_fields": {"auth_id": member.auth_id, "error": str(e)}
                    },
                )

        # Send approval email to coach
        await send_email(
            to_email=coach.member.email,
            subject="Congratulations! Your SwimBuddz Coach Application is Approved",
            body=(
                f"Hi {coach.display_name or coach.member.first_name},\n\n"
                "We are thrilled to welcome you as an approved SwimBuddz coach!\n\n"
                "You can now access your coach dashboard, complete your onboarding profile, "
                "and start creating sessions.\n\n"
                "Log in here: https://swimbuddz.com/dashboard\n\n"
                "Welcome to the team!\n"
                "The SwimBuddz Team"
            ),
        )

        return {"message": "Coach application approved", "status": "approved"}


@admin_router.post("/applications/{coach_profile_id}/reject")
async def reject_coach_application(
    coach_profile_id: str,
    data: AdminRejectCoach,
    admin: AuthUser = Depends(require_admin),
):
    """Reject a coach application (admin only)."""
    admin_email = admin.email or "admin"

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

        # Send rejection email to coach
        await send_email(
            to_email=coach.member.email,
            subject="Update on your SwimBuddz Coach Application",
            body=(
                f"Hi {coach.display_name or coach.member.first_name},\n\n"
                "Thank you for your interest in becoming a SwimBuddz coach.\n\n"
                "After careful review, we are unable to approve your application at this time.\n\n"
                f"Reason: {data.rejection_reason}\n\n"
                "You may re-apply in the future if your qualifications change.\n\n"
                "Best regards,\n"
                "The SwimBuddz Team"
            ),
        )

        return {"message": "Coach application rejected", "status": "rejected"}


@admin_router.post("/applications/{coach_profile_id}/request-info")
async def request_more_info(
    coach_profile_id: str,
    data: AdminRequestMoreInfo,
    admin: AuthUser = Depends(require_admin),
):
    """Request more information from a coach applicant (admin only)."""
    admin_email = admin.email or "admin"

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

        # Send email to coach requesting more info
        await send_email(
            to_email=coach.member.email,
            subject="Action Required: Additional Information for SwimBuddz Coach Application",
            body=(
                f"Hi {coach.display_name or coach.member.first_name},\n\n"
                "We are reviewing your coach application and need some additional information "
                "before we can proceed.\n\n"
                f"Request: {data.message}\n\n"
                "Please log in to your dashboard to update your application or reply to this email.\n\n"
                "Best regards,\n"
                "The SwimBuddz Team"
            ),
        )

        return {"message": "More info requested", "status": "more_info_needed"}
