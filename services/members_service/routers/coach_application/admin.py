"""Admin coach-application review endpoints."""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_url
from libs.common.supabase import get_supabase_admin_client
from libs.db.config import AsyncSessionLocal
from services.members_service.models import CoachBankAccount, CoachProfile, Member
from services.members_service.schemas import (
    AdminApproveCoach,
    AdminCoachApplicationDetail,
    AdminCoachApplicationListItem,
    AdminRejectCoach,
    AdminRequestMoreInfo,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from ._shared import _ensure_wallet_exists

logger = get_logger(__name__)
settings = get_settings()
router = APIRouter()


@router.get("/", response_model=list[AdminCoachApplicationListItem])
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


@router.get("/applications", response_model=list[AdminCoachApplicationListItem])
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


@router.get(
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


@router.post("/applications/{coach_profile_id}/approve")
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

        if coach.member and coach.member.auth_id:
            await _ensure_wallet_exists(str(coach.member.id), coach.member.auth_id)

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
        await email_client.send_template(
            template_type="coach_application_approved",
            to_email=coach.member.email,
            template_data={
                "coach_name": coach.display_name or coach.member.first_name,
                "onboarding_url": onboarding_link,
            },
        )

        return {"message": "Coach application approved", "status": "approved"}


@router.post("/applications/{coach_profile_id}/reject")
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
        await email_client.send_template(
            template_type="coach_application_rejected",
            to_email=coach.member.email,
            template_data={
                "coach_name": coach.display_name or coach.member.first_name,
                "rejection_reason": data.rejection_reason,
            },
        )

        return {"message": "Coach application rejected", "status": "rejected"}


@router.post("/applications/{coach_profile_id}/request-info")
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
        await email_client.send_template(
            template_type="coach_application_more_info",
            to_email=coach.member.email,
            template_data={
                "coach_name": coach.display_name or coach.member.first_name,
                "message": data.message,
            },
        )

        return {"message": "More info requested", "status": "more_info_needed"}


@router.delete("/applications/{coach_profile_id}")
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
