"""Coach-specific API routes for application, onboarding, and profile management."""

import asyncio
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_url
from libs.common.supabase import get_supabase_admin_client
from libs.db.config import AsyncSessionLocal
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from .coach_schemas import (
    AdminApproveCoach,
    AdminCoachApplicationDetail,
    AdminCoachApplicationListItem,
    AdminRejectCoach,
    AdminRequestMoreInfo,
    AdminUpdateCoachGrades,
    AgreementContentResponse,
    AgreementVersionDetail,
    AgreementVersionListItem,
    BankAccountCreate,
    BankAccountResponse,
    BankListResponse,
    CoachAgreementHistoryItem,
    CoachAgreementResponse,
    CoachAgreementStatusResponse,
    CoachApplicationCreate,
    CoachApplicationResponse,
    CoachApplicationStatusResponse,
    CoachGradesResponse,
    CoachOnboardingUpdate,
    CoachPreferencesUpdate,
    CoachProfileUpdate,
    CoachProgressionStats,
    CreateAgreementVersionRequest,
    CreateHandbookVersionRequest,
    EligibleCoachListItem,
    HandbookContentResponse,
    HandbookVersionDetail,
    HandbookVersionListItem,
    ProgramCategoryEnum,
    ResolveAccountRequest,
    ResolveAccountResponse,
    SignAgreementRequest,
    SignatureTypeEnum,
)
from .models import (
    AgreementVersion,
    CoachAgreement,
    CoachBankAccount,
    CoachGrade,
    CoachProfile,
    HandbookVersion,
    Member,
)

logger = get_logger(__name__)


def _strip_internal_handbook_sections(content: str) -> str:
    """
    Coaches should not see internal-only appendices (e.g. Appendix B: system integration spec).
    Filter at the API boundary (defense in depth, even if the frontend also hides it).
    """
    m = re.search(r"^##\s+Appendix\s+B\b.*$", content, flags=re.MULTILINE)
    if not m:
        return content
    return content[: m.start()].rstrip() + "\n"


settings = get_settings()

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
        show_in_directory=coach.show_in_directory,
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


@admin_router.get("/", response_model=list[AdminCoachApplicationListItem])
async def list_coaches_for_admin(
    status: Optional[str] = "approved,active",
    _admin: dict = Depends(require_admin),
):
    """
    List coaches for admin use (e.g., coach picker in cohort forms).
    Default filters to approved and active coaches only.
    Use status=all to get all coaches regardless of status.
    """
    async with AsyncSessionLocal() as session:
        query = (
            select(CoachProfile).join(Member).options(selectinload(CoachProfile.member))
        )

        # Parse status filter (comma-separated)
        if status and status.lower() != "all":
            statuses = [s.strip() for s in status.split(",")]
            query = query.where(CoachProfile.status.in_(statuses))

        query = query.order_by(Member.first_name, Member.last_name)

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

        # Send approval email to coach via centralized email service
        frontend_base = settings.FRONTEND_URL.rstrip("/")
        onboarding_link = f"{frontend_base}/coach/onboarding"
        email_client = get_email_client()
        await email_client.send(
            to_email=coach.member.email,
            subject="Congratulations! Your SwimBuddz Coach Application is Approved",
            body=(
                f"Hi {coach.display_name or coach.member.first_name},\n\n"
                "We are thrilled to welcome you as an approved SwimBuddz coach!\n\n"
                "Please complete your coach onboarding to activate your profile and "
                "start coaching.\n\n"
                f"Complete onboarding: {onboarding_link}\n\n"
                "If you haven't logged in yet, you'll be prompted to sign in first.\n\n"
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

        # Send rejection email to coach via centralized email service
        email_client = get_email_client()
        await email_client.send(
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

        # Send email to coach requesting more info via centralized email service
        email_client = get_email_client()
        await email_client.send(
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


@admin_router.delete("/applications/{coach_profile_id}")
async def delete_coach_application(
    coach_profile_id: str,
    admin: AuthUser = Depends(require_admin),
):
    """Delete a coach profile so the member can re-apply from scratch."""
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

        member = coach.member
        if member:
            member_auth_id = member.auth_id

        # Remove any associated bank account for a clean re-application
        if member:
            await session.execute(
                delete(CoachBankAccount).where(CoachBankAccount.member_id == member.id)
            )

            # Remove coach role from member record
            if member.roles:
                member.roles = [r for r in member.roles if r != "coach"]

        # Delete coach profile (coach_agreements cascade)
        await session.delete(coach)
        await session.commit()

        logger.info(
            f"Coach application {coach_profile_id} deleted by {admin_email}",
            extra={"extra_fields": {"coach_profile_id": coach_profile_id}},
        )

    # Remove coach role from Supabase app_metadata (if present)
    if member_auth_id:
        try:
            admin_supabase = get_supabase_admin_client()
            current_user = await asyncio.to_thread(
                admin_supabase.auth.admin.get_user_by_id, member_auth_id
            )
            existing_roles = []
            if current_user and current_user.user:
                existing_metadata = getattr(current_user.user, "app_metadata", {}) or {}
                existing_roles = existing_metadata.get("roles", [])

            if "coach" in existing_roles:
                new_roles = [r for r in existing_roles if r != "coach"]
                await asyncio.to_thread(
                    admin_supabase.auth.admin.update_user_by_id,
                    member_auth_id,
                    {"app_metadata": {"roles": new_roles}},
                )
        except Exception as e:
            logger.warning(
                f"Could not update Supabase app_metadata for coach deletion: {e}",
                extra={"extra_fields": {"auth_id": member_auth_id}},
            )

    return {"message": "Coach application deleted"}


# === Coach Bank Account Endpoints ===


@router.get("/me/bank-account")
async def get_my_bank_account(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the current coach's bank account."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Get member
        result = await session.execute(select(Member).where(Member.auth_id == auth_id))
        member = result.scalar_one_or_none()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

        # Get bank account
        result = await session.execute(
            select(CoachBankAccount).where(CoachBankAccount.member_id == member.id)
        )
        bank_account = result.scalar_one_or_none()

        if not bank_account:
            raise HTTPException(status_code=404, detail="No bank account found")

        return BankAccountResponse(
            id=str(bank_account.id),
            member_id=str(bank_account.member_id),
            bank_code=bank_account.bank_code,
            bank_name=bank_account.bank_name,
            account_number=bank_account.account_number,
            account_name=bank_account.account_name,
            is_verified=bank_account.is_verified,
            verified_at=bank_account.verified_at,
            paystack_recipient_code=bank_account.paystack_recipient_code,
            created_at=bank_account.created_at,
            updated_at=bank_account.updated_at,
        )


@router.post("/me/bank-account")
async def create_or_update_bank_account(
    data: BankAccountCreate,
    current_user: AuthUser = Depends(get_current_user),
):
    """
    Create or update coach's bank account.

    Auto-verifies via Paystack Resolve Account API and creates
    a transfer recipient for automated payouts.
    """
    from datetime import timezone

    from services.payments_service.paystack_client import PaystackClient, PaystackError

    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Get member
        result = await session.execute(select(Member).where(Member.auth_id == auth_id))
        member = result.scalar_one_or_none()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

        # Verify account via Paystack
        paystack = PaystackClient()
        try:
            resolved = await paystack.resolve_account(
                account_number=data.account_number,
                bank_code=data.bank_code,
            )
            account_name = resolved.account_name
        except PaystackError as e:
            raise HTTPException(
                status_code=400, detail=f"Could not verify bank account: {e.message}"
            )

        # Get or create bank account record
        result = await session.execute(
            select(CoachBankAccount).where(CoachBankAccount.member_id == member.id)
        )
        bank_account = result.scalar_one_or_none()

        if not bank_account:
            bank_account = CoachBankAccount(member_id=member.id)
            session.add(bank_account)

        # Update details
        bank_account.bank_code = data.bank_code
        bank_account.bank_name = data.bank_name
        bank_account.account_number = data.account_number
        bank_account.account_name = account_name
        bank_account.is_verified = True
        bank_account.verified_at = datetime.now(timezone.utc)
        bank_account.verified_by = "paystack_api"

        # Create Paystack transfer recipient
        try:
            recipient = await paystack.create_transfer_recipient(
                account_number=data.account_number,
                bank_code=data.bank_code,
                name=account_name,
            )
            bank_account.paystack_recipient_code = recipient.recipient_code
            logger.info(
                f"Created Paystack transfer recipient for member {member.id}",
                extra={"extra_fields": {"recipient_code": recipient.recipient_code}},
            )
        except PaystackError as e:
            # Log but don't fail - recipient can be created later
            logger.warning(
                f"Could not create Paystack transfer recipient: {e.message}",
                extra={
                    "extra_fields": {"member_id": str(member.id), "error": e.message}
                },
            )

        await session.commit()
        await session.refresh(bank_account)

        return BankAccountResponse(
            id=str(bank_account.id),
            member_id=str(bank_account.member_id),
            bank_code=bank_account.bank_code,
            bank_name=bank_account.bank_name,
            account_number=bank_account.account_number,
            account_name=bank_account.account_name,
            is_verified=bank_account.is_verified,
            verified_at=bank_account.verified_at,
            paystack_recipient_code=bank_account.paystack_recipient_code,
            created_at=bank_account.created_at,
            updated_at=bank_account.updated_at,
        )


@router.delete("/me/bank-account")
async def delete_bank_account(
    current_user: AuthUser = Depends(get_current_user),
):
    """Delete coach's bank account."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Member).where(Member.auth_id == auth_id))
        member = result.scalar_one_or_none()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

        result = await session.execute(
            select(CoachBankAccount).where(CoachBankAccount.member_id == member.id)
        )
        bank_account = result.scalar_one_or_none()

        if not bank_account:
            raise HTTPException(status_code=404, detail="No bank account found")

        await session.delete(bank_account)
        await session.commit()

        return {"message": "Bank account deleted"}


@router.get("/banks")
async def list_banks():
    """
    Get list of Nigerian banks for dropdown.
    Cached via Paystack API.
    """
    from services.payments_service.paystack_client import PaystackClient, PaystackError

    paystack = PaystackClient()
    try:
        banks = await paystack.list_banks(country="nigeria")
        return [
            BankListResponse(name=b.name, code=b.code, slug=b.slug)
            for b in banks
            if b.is_active
        ]
    except PaystackError as e:
        raise HTTPException(
            status_code=500, detail=f"Could not fetch banks: {e.message}"
        )


@router.post("/resolve-account")
async def resolve_bank_account(
    data: ResolveAccountRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    """
    Verify a bank account and get the account holder name.
    Free Paystack API, used for validation before saving.
    """
    from services.payments_service.paystack_client import PaystackClient, PaystackError

    paystack = PaystackClient()
    try:
        resolved = await paystack.resolve_account(
            account_number=data.account_number,
            bank_code=data.bank_code,
        )
        return ResolveAccountResponse(
            account_number=resolved.account_number,
            account_name=resolved.account_name,
            bank_code=resolved.bank_code,
        )
    except PaystackError as e:
        raise HTTPException(
            status_code=400, detail=f"Could not verify account: {e.message}"
        )


# ============================================================================
# COACH GRADES & PROGRESSION ENDPOINTS
# ============================================================================

# Map category enum to model field names
CATEGORY_TO_FIELD = {
    ProgramCategoryEnum.LEARN_TO_SWIM: "learn_to_swim_grade",
    ProgramCategoryEnum.SPECIAL_POPULATIONS: "special_populations_grade",
    ProgramCategoryEnum.INSTITUTIONAL: "institutional_grade",
    ProgramCategoryEnum.COMPETITIVE_ELITE: "competitive_elite_grade",
    ProgramCategoryEnum.CERTIFICATIONS: "certifications_grade",
    ProgramCategoryEnum.SPECIALIZED_DISCIPLINES: "specialized_disciplines_grade",
    ProgramCategoryEnum.ADJACENT_SERVICES: "adjacent_services_grade",
}


@router.get("/me/grades", response_model=CoachGradesResponse)
async def get_my_grades(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the current coach's grades across all categories."""
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

        return CoachGradesResponse(
            coach_profile_id=str(coach.id),
            member_id=str(member.id),
            display_name=coach.display_name,
            learn_to_swim_grade=coach.learn_to_swim_grade,
            special_populations_grade=coach.special_populations_grade,
            institutional_grade=coach.institutional_grade,
            competitive_elite_grade=coach.competitive_elite_grade,
            certifications_grade=coach.certifications_grade,
            specialized_disciplines_grade=coach.specialized_disciplines_grade,
            adjacent_services_grade=coach.adjacent_services_grade,
            total_coaching_hours=coach.total_coaching_hours,
            cohorts_completed=coach.cohorts_completed,
            average_feedback_rating=coach.average_feedback_rating,
            swimbuddz_level=coach.swimbuddz_level,
            last_active_date=coach.last_active_date,
            first_aid_cert_expiry=coach.first_aid_cert_expiry,
            cpr_expiry_date=coach.cpr_expiry_date,
            lifeguard_expiry_date=coach.lifeguard_expiry_date,
        )


@router.get("/me/progression", response_model=CoachProgressionStats)
async def get_my_progression(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the current coach's progression statistics."""
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

        # Determine highest grade and grades held
        grade_order = [CoachGrade.GRADE_1, CoachGrade.GRADE_2, CoachGrade.GRADE_3]
        grades_held = []
        highest_grade = None
        highest_level = -1

        for category, field_name in CATEGORY_TO_FIELD.items():
            grade = getattr(coach, field_name)
            if grade:
                grades_held.append(category.value)
                level = grade_order.index(grade)
                if level > highest_level:
                    highest_level = level
                    highest_grade = grade

        # Check for expiring credentials (within 30 days)
        from datetime import date, timedelta

        today = date.today()
        expiring_soon = []
        credentials_valid = True

        if coach.first_aid_cert_expiry:
            if coach.first_aid_cert_expiry < today:
                credentials_valid = False
                expiring_soon.append("first_aid_expired")
            elif coach.first_aid_cert_expiry <= today + timedelta(days=30):
                expiring_soon.append("first_aid")

        if coach.cpr_expiry_date:
            cpr_date = coach.cpr_expiry_date.date()
            if cpr_date < today:
                credentials_valid = False
                expiring_soon.append("cpr_expired")
            elif cpr_date <= today + timedelta(days=30):
                expiring_soon.append("cpr")

        if coach.lifeguard_expiry_date:
            lifeguard_date = coach.lifeguard_expiry_date.date()
            if lifeguard_date < today:
                credentials_valid = False
                expiring_soon.append("lifeguard_expired")
            elif lifeguard_date <= today + timedelta(days=30):
                expiring_soon.append("lifeguard")

        # TODO: Get active cohorts count from academy service
        active_cohorts = 0

        return CoachProgressionStats(
            coach_profile_id=str(coach.id),
            total_coaching_hours=coach.total_coaching_hours,
            cohorts_completed=coach.cohorts_completed,
            active_cohorts=active_cohorts,
            average_feedback_rating=coach.average_feedback_rating,
            swimbuddz_level=coach.swimbuddz_level,
            highest_grade=highest_grade,
            grades_held=grades_held,
            credentials_valid=credentials_valid,
            expiring_soon=expiring_soon,
        )


# === Admin Grade Management Endpoints ===


@admin_router.get("/{coach_profile_id}/grades", response_model=CoachGradesResponse)
async def get_coach_grades(
    coach_profile_id: str,
    _admin: dict = Depends(require_admin),
):
    """Get a coach's grades (admin only)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CoachProfile)
            .options(selectinload(CoachProfile.member))
            .where(CoachProfile.id == coach_profile_id)
        )
        coach = result.scalar_one_or_none()

        if not coach:
            raise HTTPException(status_code=404, detail="Coach profile not found")

        return CoachGradesResponse(
            coach_profile_id=str(coach.id),
            member_id=str(coach.member_id),
            display_name=coach.display_name,
            learn_to_swim_grade=coach.learn_to_swim_grade,
            special_populations_grade=coach.special_populations_grade,
            institutional_grade=coach.institutional_grade,
            competitive_elite_grade=coach.competitive_elite_grade,
            certifications_grade=coach.certifications_grade,
            specialized_disciplines_grade=coach.specialized_disciplines_grade,
            adjacent_services_grade=coach.adjacent_services_grade,
            total_coaching_hours=coach.total_coaching_hours,
            cohorts_completed=coach.cohorts_completed,
            average_feedback_rating=coach.average_feedback_rating,
            swimbuddz_level=coach.swimbuddz_level,
            last_active_date=coach.last_active_date,
            first_aid_cert_expiry=coach.first_aid_cert_expiry,
            cpr_expiry_date=coach.cpr_expiry_date,
            lifeguard_expiry_date=coach.lifeguard_expiry_date,
        )


@admin_router.put("/{coach_profile_id}/grades", response_model=CoachGradesResponse)
async def update_coach_grades(
    coach_profile_id: str,
    data: AdminUpdateCoachGrades,
    admin: AuthUser = Depends(require_admin),
):
    """Update a coach's grades (admin only)."""
    admin_email = admin.email or "admin"

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CoachProfile)
            .options(selectinload(CoachProfile.member))
            .where(CoachProfile.id == coach_profile_id)
        )
        coach = result.scalar_one_or_none()

        if not coach:
            raise HTTPException(status_code=404, detail="Coach profile not found")

        # Update grades that were provided
        update_data = data.model_dump(exclude_unset=True, exclude={"admin_notes"})
        for field, value in update_data.items():
            if hasattr(coach, field) and value is not None:
                setattr(coach, field, value)

        # Update admin notes if provided
        if data.admin_notes:
            existing_notes = coach.admin_notes or ""
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            new_note = (
                f"\n[{timestamp}] Grade update by {admin_email}: {data.admin_notes}"
            )
            coach.admin_notes = existing_notes + new_note

        await session.commit()
        await session.refresh(coach)

        logger.info(
            f"Coach grades updated for {coach_profile_id} by {admin_email}",
            extra={"extra_fields": {"grades": update_data}},
        )

        return CoachGradesResponse(
            coach_profile_id=str(coach.id),
            member_id=str(coach.member_id),
            display_name=coach.display_name,
            learn_to_swim_grade=coach.learn_to_swim_grade,
            special_populations_grade=coach.special_populations_grade,
            institutional_grade=coach.institutional_grade,
            competitive_elite_grade=coach.competitive_elite_grade,
            certifications_grade=coach.certifications_grade,
            specialized_disciplines_grade=coach.specialized_disciplines_grade,
            adjacent_services_grade=coach.adjacent_services_grade,
            total_coaching_hours=coach.total_coaching_hours,
            cohorts_completed=coach.cohorts_completed,
            average_feedback_rating=coach.average_feedback_rating,
            swimbuddz_level=coach.swimbuddz_level,
            last_active_date=coach.last_active_date,
            first_aid_cert_expiry=coach.first_aid_cert_expiry,
            cpr_expiry_date=coach.cpr_expiry_date,
            lifeguard_expiry_date=coach.lifeguard_expiry_date,
        )


@admin_router.get(
    "/eligible/{category}/{required_grade}",
    response_model=list[EligibleCoachListItem],
)
async def list_eligible_coaches(
    category: ProgramCategoryEnum,
    required_grade: str,
    _admin: dict = Depends(require_admin),
):
    """
    List coaches eligible for a cohort based on category and required grade.

    A coach is eligible if their grade for the category meets or exceeds
    the required grade. Grade hierarchy: GRADE_1 < GRADE_2 < GRADE_3.
    """
    # Validate and convert required_grade
    try:
        required = CoachGrade(required_grade)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid grade: {required_grade}. Must be grade_1, grade_2, or grade_3",
        )

    grade_field = CATEGORY_TO_FIELD.get(category)
    if not grade_field:
        raise HTTPException(status_code=400, detail=f"Invalid category: {category}")

    # Determine eligible grades (required and above)
    grade_order = [CoachGrade.GRADE_1, CoachGrade.GRADE_2, CoachGrade.GRADE_3]
    required_level = grade_order.index(required)
    eligible_grades = grade_order[required_level:]

    async with AsyncSessionLocal() as session:
        # Get coaches with the appropriate grade
        result = await session.execute(
            select(CoachProfile)
            .join(Member)
            .options(selectinload(CoachProfile.member))
            .where(
                CoachProfile.status.in_(["approved", "active"]),
                getattr(CoachProfile, grade_field).in_(eligible_grades),
            )
            .order_by(Member.first_name, Member.last_name)
        )
        profiles = result.scalars().all()

        return [
            EligibleCoachListItem(
                coach_profile_id=str(p.id),
                member_id=str(p.member_id),
                display_name=p.display_name,
                email=p.member.email,
                grade=getattr(p, grade_field),
                coaching_years=p.coaching_years or 0,
                average_rating=p.average_rating or 0.0,
                cohorts_completed=p.cohorts_completed,
                # TODO: Check active cohorts against max_cohorts_at_once
                is_available=True,
            )
            for p in profiles
        ]


# ============================================================================
# COACH AGREEMENT ENDPOINTS
# ============================================================================

import hashlib


def _compute_agreement_hash(content: str) -> str:
    """Compute SHA-256 hash of agreement content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _render_agreement_for_coach(
    template_content: str,
    member: "Member",
    coach_profile: "CoachProfile",
) -> str:
    """Render agreement template by replacing placeholders with coach data.

    Placeholders replaced:
      [DATE]             -> today's date
      [COACH FULL NAME]  -> member first + last name
      [Coach Address]    -> from member profile (address, city, state)
      [Phone Number]     -> from member profile
      [Email Address]    -> from member record
      [PERCENTAGE]       -> "See Coach Handbook" (varies by assignment)
      [X]                -> highest grade number
      [Category]         -> highest grade category
      [GRADE LEVEL]      -> current grade description
    """
    from datetime import date as date_type

    profile = member.profile

    # Build address string
    address_parts = []
    if profile and profile.address:
        address_parts.append(profile.address)
    if profile and profile.city:
        address_parts.append(profile.city)
    if profile and profile.state:
        address_parts.append(profile.state)
    address_str = ", ".join(address_parts) if address_parts else "Not provided"

    phone_str = profile.phone if profile and profile.phone else "Not provided"
    full_name = f"{member.first_name} {member.last_name}"

    # Determine highest grade from category grades
    grade_fields = {
        "Learn to Swim": coach_profile.learn_to_swim_grade,
        "Special Populations": coach_profile.special_populations_grade,
        "Institutional": coach_profile.institutional_grade,
        "Competitive/Elite": coach_profile.competitive_elite_grade,
        "Certifications": coach_profile.certifications_grade,
        "Specialized Disciplines": coach_profile.specialized_disciplines_grade,
        "Adjacent Services": coach_profile.adjacent_services_grade,
    }
    grade_order = {"grade_1": 1, "grade_2": 2, "grade_3": 3}
    highest_grade = None
    highest_category = None
    highest_num = 0
    for category, grade_val in grade_fields.items():
        if grade_val and grade_order.get(grade_val, 0) > highest_num:
            highest_num = grade_order[grade_val]
            highest_grade = grade_val
            highest_category = category

    grade_labels = {
        "grade_1": "Grade 1 – Foundational",
        "grade_2": "Grade 2 – Technical",
        "grade_3": "Grade 3 – Advanced/Specialist",
    }
    has_grades = highest_grade is not None

    # Perform replacements
    rendered = template_content
    rendered = rendered.replace("[DATE]", date_type.today().strftime("%B %d, %Y"))
    rendered = rendered.replace("[COACH FULL NAME]", full_name)
    rendered = rendered.replace("[Coach Address]", address_str)
    rendered = rendered.replace("[Phone Number]", phone_str)
    rendered = rendered.replace("[Email Address]", member.email)

    # Grade level
    if has_grades:
        rendered = rendered.replace("[GRADE LEVEL]", grade_labels[highest_grade])
    else:
        rendered = rendered.replace(
            "Current Grade: **[GRADE LEVEL]**",
            "Current Grade: **To be determined upon assignment**",
        )
        # Fallback if pattern doesn't match exactly
        rendered = rendered.replace("[GRADE LEVEL]", "To be determined upon assignment")

    # Revenue share line: **[PERCENTAGE]%** (Grade [X], [Category])
    # The template has: **[PERCENTAGE]%** (Grade [X], [Category])
    if has_grades:
        grade_num_str = str(highest_num)
        rendered = rendered.replace(
            "**[PERCENTAGE]%** (Grade [X], [Category])",
            f"**See Coach Handbook** (Grade {grade_num_str}, {highest_category})",
        )
    else:
        rendered = rendered.replace(
            "**[PERCENTAGE]%** (Grade [X], [Category])",
            "**To be determined upon grade assignment** (see Coach Handbook for pay bands)",
        )

    # Fallback for any remaining individual placeholders
    rendered = rendered.replace("[PERCENTAGE]", "TBD")
    rendered = rendered.replace("[X]", str(highest_num) if has_grades else "TBD")
    rendered = rendered.replace("[Category]", highest_category or "TBD")

    return rendered


async def _get_current_agreement_version(session) -> AgreementVersion:
    """Get the current agreement version from the database.

    Raises HTTPException(404) if no current version exists.
    """
    result = await session.execute(
        select(AgreementVersion).where(AgreementVersion.is_current.is_(True))
    )
    current = result.scalar_one_or_none()
    if not current:
        raise HTTPException(
            status_code=404,
            detail="No current agreement version found. Contact an administrator.",
        )
    return current


@router.get("/agreement/current", response_model=AgreementContentResponse)
async def get_current_agreement(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the current coach agreement content, rendered with the coach's data.

    Placeholders like [COACH FULL NAME], [DATE], [Email Address] etc. are
    replaced with the authenticated coach's real data at fetch time.
    The content_hash still reflects the original template so that signing
    verification remains consistent across all coaches.
    """
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        current = await _get_current_agreement_version(session)

        # Fetch member + profile + coach profile for placeholder rendering
        result = await session.execute(
            select(Member)
            .options(
                selectinload(Member.profile),
                selectinload(Member.coach_profile),
            )
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if member and member.coach_profile:
            rendered_content = _render_agreement_for_coach(
                current.content, member, member.coach_profile
            )
        else:
            # Fallback: return raw template if no coach profile
            rendered_content = current.content

        return AgreementContentResponse(
            version=current.version,
            title=current.title,
            content=rendered_content,
            content_hash=current.content_hash,  # Hash of template, not rendered
            effective_date=current.effective_date,
            requires_signature=True,
        )


@router.get("/agreement/status", response_model=CoachAgreementStatusResponse)
async def get_agreement_status(
    current_user: AuthUser = Depends(get_current_user),
):
    """Check if the current coach has signed the latest agreement."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Get current agreement version from DB
        current_av = await _get_current_agreement_version(session)
        current_version_str = current_av.version

        # Get member and coach profile
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile

        # Check for active agreement
        result = await session.execute(
            select(CoachAgreement)
            .where(
                CoachAgreement.coach_profile_id == coach.id,
                CoachAgreement.is_active.is_(True),
            )
            .order_by(CoachAgreement.signed_at.desc())
            .limit(1)
        )
        active_agreement = result.scalar_one_or_none()

        if not active_agreement:
            return CoachAgreementStatusResponse(
                has_signed_current_version=False,
                current_version=current_version_str,
                signed_version=None,
                signed_at=None,
                requires_new_signature=True,
            )

        # Check if signed version matches current
        has_signed_current = active_agreement.agreement_version == current_version_str

        return CoachAgreementStatusResponse(
            has_signed_current_version=has_signed_current,
            current_version=current_version_str,
            signed_version=active_agreement.agreement_version,
            signed_at=active_agreement.signed_at,
            requires_new_signature=not has_signed_current,
        )


@router.post("/agreement/sign", response_model=CoachAgreementResponse)
async def sign_agreement(
    data: SignAgreementRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    """Sign the coach agreement."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Verify agreement version exists and is current
        result = await session.execute(
            select(AgreementVersion).where(
                AgreementVersion.is_current.is_(True),
                AgreementVersion.version == data.agreement_version,
            )
        )
        current_av = result.scalar_one_or_none()

        if not current_av:
            raise HTTPException(
                status_code=400,
                detail=f"Agreement version {data.agreement_version} is not the current version.",
            )

        if data.agreement_content_hash != current_av.content_hash:
            raise HTTPException(
                status_code=400,
                detail="Agreement content has changed. Please refresh and try again.",
            )

        # Get member and coach profile
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile

        # Mark any existing active agreements as superseded
        result = await session.execute(
            select(CoachAgreement).where(
                CoachAgreement.coach_profile_id == coach.id,
                CoachAgreement.is_active.is_(True),
            )
        )
        existing_agreements = result.scalars().all()

        # Validate handbook acknowledgment
        if not data.handbook_acknowledged:
            raise HTTPException(
                status_code=400,
                detail="You must acknowledge the Coach Handbook before signing the agreement.",
            )

        # Validate signature type specific requirements
        if (
            data.signature_type == SignatureTypeEnum.UPLOADED_IMAGE
            and not data.signature_media_id
        ):
            raise HTTPException(
                status_code=400,
                detail="signature_media_id is required when signature_type is uploaded_image.",
            )

        # For checkbox, auto-set signature_data
        sig_data = data.signature_data
        if data.signature_type == SignatureTypeEnum.CHECKBOX:
            sig_data = f"CHECKBOX_AGREE:{datetime.now(timezone.utc).isoformat()}"

        import uuid

        # Create new agreement
        new_agreement = CoachAgreement(
            coach_profile_id=coach.id,
            agreement_version=data.agreement_version,
            agreement_content_hash=data.agreement_content_hash,
            signature_type=data.signature_type.value,
            signature_data=sig_data,
            signature_media_id=uuid.UUID(data.signature_media_id)
            if data.signature_media_id
            else None,
            signed_at=datetime.now(timezone.utc),
            handbook_acknowledged=True,
            handbook_version=data.handbook_version,
            ip_address=None,  # Would get from request in production
            user_agent=None,  # Would get from request in production
            is_active=True,
        )
        session.add(new_agreement)

        # Supersede old agreements
        for old_agreement in existing_agreements:
            old_agreement.is_active = False
            old_agreement.superseded_by_id = new_agreement.id
            old_agreement.superseded_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(new_agreement)

        logger.info(
            f"Coach agreement signed: coach={coach.id}, version={data.agreement_version}",
            extra={
                "extra_fields": {
                    "coach_profile_id": str(coach.id),
                    "agreement_version": data.agreement_version,
                }
            },
        )

        return CoachAgreementResponse(
            id=str(new_agreement.id),
            coach_profile_id=str(new_agreement.coach_profile_id),
            agreement_version=new_agreement.agreement_version,
            signature_type=new_agreement.signature_type,
            signed_at=new_agreement.signed_at,
            is_active=new_agreement.is_active,
            ip_address=None,  # Don't expose full IP
            created_at=new_agreement.created_at,
        )


@router.get("/agreement/history", response_model=list[CoachAgreementHistoryItem])
async def get_agreement_history(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the coach's agreement signing history."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Get member and coach profile
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile

        # Get all agreements
        result = await session.execute(
            select(CoachAgreement)
            .where(CoachAgreement.coach_profile_id == coach.id)
            .order_by(CoachAgreement.signed_at.desc())
        )
        agreements = result.scalars().all()

        return [
            CoachAgreementHistoryItem(
                id=str(a.id),
                agreement_version=a.agreement_version,
                signature_type=a.signature_type,
                signed_at=a.signed_at,
                is_active=a.is_active,
                superseded_at=a.superseded_at,
            )
            for a in agreements
        ]


# ============================================================================
# ADMIN AGREEMENT VERSION MANAGEMENT
# ============================================================================


@admin_router.get("/agreements", response_model=list[AgreementVersionListItem])
async def list_agreement_versions(
    _admin: dict = Depends(require_admin),
):
    """List all agreement versions with signature counts (admin only)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgreementVersion).order_by(AgreementVersion.created_at.desc())
        )
        versions = result.scalars().all()

        items = []
        for v in versions:
            # Count signatures for this version
            sig_result = await session.execute(
                select(func.count(CoachAgreement.id)).where(
                    CoachAgreement.agreement_version == v.version
                )
            )
            sig_count = sig_result.scalar() or 0

            items.append(
                AgreementVersionListItem(
                    id=str(v.id),
                    version=v.version,
                    title=v.title,
                    effective_date=v.effective_date,
                    is_current=v.is_current,
                    content_hash=v.content_hash,
                    signature_count=sig_count,
                    created_at=v.created_at,
                )
            )
        return items


@admin_router.post("/agreements", response_model=AgreementVersionDetail)
async def create_agreement_version(
    data: CreateAgreementVersionRequest,
    admin: AuthUser = Depends(require_admin),
):
    """Create a new agreement version (admin only).

    Auto-sets the new version as current and deactivates the previous one.
    Sends email notification to all active coaches about the new version.
    """
    admin_id = admin.user_id
    content_hash = _compute_agreement_hash(data.content)

    async with AsyncSessionLocal() as session:
        # Check version doesn't already exist
        existing = await session.execute(
            select(AgreementVersion).where(AgreementVersion.version == data.version)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Version {data.version} already exists",
            )

        # Deactivate all current versions
        result = await session.execute(
            select(AgreementVersion).where(AgreementVersion.is_current.is_(True))
        )
        for old_version in result.scalars().all():
            old_version.is_current = False

        # Create new version
        new_version = AgreementVersion(
            version=data.version,
            title=data.title,
            content=data.content,
            content_hash=content_hash,
            effective_date=data.effective_date,
            is_current=True,
            created_by_id=admin_id,
        )
        session.add(new_version)
        await session.commit()
        await session.refresh(new_version)

        # Send notification emails to active coaches (non-blocking)
        try:
            coach_result = await session.execute(
                select(CoachProfile)
                .join(Member)
                .options(selectinload(CoachProfile.member))
                .where(CoachProfile.status == "active")
            )
            active_coaches = coach_result.scalars().all()

            if active_coaches:
                coach_emails = [
                    c.member.email
                    for c in active_coaches
                    if c.member and c.member.email
                ]
                if coach_emails:
                    frontend_base = settings.FRONTEND_URL.rstrip("/")
                    agreement_link = f"{frontend_base}/coach/agreement"

                    email_client = get_email_client()
                    await email_client.send_bulk(
                        to_emails=coach_emails,
                        subject=f"New Coach Agreement Version {data.version} — Signature Required",
                        body=(
                            f"Hi Coach,\n\n"
                            f"A new version ({data.version}) of the SwimBuddz Coach Agreement is now available.\n\n"
                            f"Please review and sign the updated agreement at your earliest convenience:\n"
                            f"{agreement_link}\n\n"
                            f"Until you sign the new agreement, some dashboard features may be restricted.\n\n"
                            f"Best regards,\n"
                            f"The SwimBuddz Team"
                        ),
                    )
                    logger.info(
                        f"Sent agreement update notification to {len(coach_emails)} coaches",
                        extra={"extra_fields": {"version": data.version}},
                    )
        except Exception as e:
            # Email failure should not block agreement creation
            logger.error(
                f"Failed to send agreement update notifications: {e}",
                extra={"extra_fields": {"version": data.version}},
            )

        return AgreementVersionDetail(
            id=str(new_version.id),
            version=new_version.version,
            title=new_version.title,
            content=new_version.content,
            content_hash=new_version.content_hash,
            effective_date=new_version.effective_date,
            is_current=new_version.is_current,
            created_by_id=str(new_version.created_by_id)
            if new_version.created_by_id
            else None,
            signature_count=0,
            active_signature_count=0,
            created_at=new_version.created_at,
            updated_at=new_version.updated_at,
        )


@admin_router.get("/agreements/{version_id}", response_model=AgreementVersionDetail)
async def get_agreement_version_detail(
    version_id: str,
    _admin: dict = Depends(require_admin),
):
    """Get a specific agreement version with signature statistics."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgreementVersion).where(AgreementVersion.id == version_id)
        )
        version = result.scalar_one_or_none()
        if not version:
            raise HTTPException(status_code=404, detail="Agreement version not found")

        # Count total signatures for this version
        sig_result = await session.execute(
            select(func.count(CoachAgreement.id)).where(
                CoachAgreement.agreement_version == version.version
            )
        )
        total_sigs = sig_result.scalar() or 0

        # Count active signatures (coaches currently on this version)
        active_sig_result = await session.execute(
            select(func.count(CoachAgreement.id)).where(
                CoachAgreement.agreement_version == version.version,
                CoachAgreement.is_active.is_(True),
            )
        )
        active_sigs = active_sig_result.scalar() or 0

        return AgreementVersionDetail(
            id=str(version.id),
            version=version.version,
            title=version.title,
            content=version.content,
            content_hash=version.content_hash,
            effective_date=version.effective_date,
            is_current=version.is_current,
            created_by_id=str(version.created_by_id) if version.created_by_id else None,
            signature_count=total_sigs,
            active_signature_count=active_sigs,
            created_at=version.created_at,
            updated_at=version.updated_at,
        )


# ============================================================================
# HANDBOOK ENDPOINTS
# ============================================================================


@router.get("/handbook/current", response_model=HandbookContentResponse)
async def get_current_handbook(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the current handbook content for display to coaches."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HandbookVersion).where(HandbookVersion.is_current.is_(True))
        )
        handbook = result.scalar_one_or_none()

        if not handbook:
            raise HTTPException(
                status_code=404,
                detail="No current handbook version found.",
            )

        return HandbookContentResponse(
            version=handbook.version,
            title=handbook.title,
            content=_strip_internal_handbook_sections(handbook.content),
            content_hash=handbook.content_hash,
            effective_date=handbook.effective_date,
        )


@router.get("/handbook/{version}", response_model=HandbookContentResponse)
async def get_handbook_version(
    version: str,
    current_user: AuthUser = Depends(get_current_user),
):
    """Get a specific handbook version."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HandbookVersion).where(HandbookVersion.version == version)
        )
        handbook = result.scalar_one_or_none()

        if not handbook:
            raise HTTPException(
                status_code=404,
                detail=f"Handbook version {version} not found.",
            )

        return HandbookContentResponse(
            version=handbook.version,
            title=handbook.title,
            content=_strip_internal_handbook_sections(handbook.content),
            content_hash=handbook.content_hash,
            effective_date=handbook.effective_date,
        )


@admin_router.get("/handbook/versions", response_model=list[HandbookVersionListItem])
async def list_handbook_versions(
    current_user: AuthUser = Depends(require_admin),
):
    """List all handbook versions (admin)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HandbookVersion).order_by(HandbookVersion.created_at.desc())
        )
        versions = result.scalars().all()

        return [
            HandbookVersionListItem(
                id=str(v.id),
                version=v.version,
                title=v.title,
                effective_date=v.effective_date,
                is_current=v.is_current,
                content_hash=v.content_hash,
                created_at=v.created_at,
            )
            for v in versions
        ]


@admin_router.post("/handbook", response_model=HandbookVersionDetail)
async def create_handbook_version(
    data: CreateHandbookVersionRequest,
    current_user: AuthUser = Depends(require_admin),
):
    """Create a new handbook version (admin). Deactivates previous current version."""
    import hashlib
    import uuid

    async with AsyncSessionLocal() as session:
        # Check if version already exists
        result = await session.execute(
            select(HandbookVersion).where(HandbookVersion.version == data.version)
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Handbook version {data.version} already exists.",
            )

        # Deactivate current version
        result = await session.execute(
            select(HandbookVersion).where(HandbookVersion.is_current.is_(True))
        )
        current = result.scalar_one_or_none()
        if current:
            current.is_current = False

        # Get admin member ID
        admin_result = await session.execute(
            select(Member).where(Member.auth_id == current_user.user_id)
        )
        admin_member = admin_result.scalar_one_or_none()

        content_hash = hashlib.sha256(data.content.encode("utf-8")).hexdigest()

        handbook = HandbookVersion(
            id=uuid.uuid4(),
            version=data.version,
            title=data.title,
            content=data.content,
            content_hash=content_hash,
            effective_date=data.effective_date,
            is_current=True,
            created_by_id=admin_member.id if admin_member else None,
        )
        session.add(handbook)
        await session.commit()
        await session.refresh(handbook)

        logger.info(
            f"Created handbook version {data.version}",
            extra={"extra_fields": {"version": data.version}},
        )

        return HandbookVersionDetail(
            id=str(handbook.id),
            version=handbook.version,
            title=handbook.title,
            content=handbook.content,
            content_hash=handbook.content_hash,
            effective_date=handbook.effective_date,
            is_current=handbook.is_current,
            created_by_id=str(handbook.created_by_id)
            if handbook.created_by_id
            else None,
            created_at=handbook.created_at,
            updated_at=handbook.updated_at,
        )
