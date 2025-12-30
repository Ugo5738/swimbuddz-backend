"""Pending registration router - handles user registration flow."""

import json

from libs.common.logging import get_logger

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import (
    Member,
    MemberAvailability,
    MemberEmergencyContact,
    MemberMembership,
    MemberPreferences,
    MemberProfile,
    PendingRegistration,
)
from services.members_service.schemas import (
    MemberResponse,
    PendingRegistrationCreate,
    PendingRegistrationResponse,
)
from services.members_service.routers._helpers import (
    member_eager_load_options,
    normalize_member_tiers,
    sync_member_roles,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/pending-registrations", tags=["pending-registrations"])


@router.post(
    "/", response_model=PendingRegistrationResponse, status_code=status.HTTP_201_CREATED
)
async def create_pending_registration(
    registration_in: PendingRegistrationCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a pending registration.
    This is called by the frontend during signup. It stores the registration intent
    and triggers Supabase Auth signup to send the confirmation email.
    """
    # Check if member already exists
    query = select(Member).where(Member.email == registration_in.email)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Check if pending registration already exists, update if so
    query = select(PendingRegistration).where(
        PendingRegistration.email == registration_in.email
    )
    result = await db.execute(query)
    pending = result.scalar_one_or_none()

    profile_data = registration_in.model_dump()
    # Remove password from stored profile data
    if "password" in profile_data:
        del profile_data["password"]

    profile_data_json = json.dumps(profile_data)

    if pending:
        pending.profile_data_json = profile_data_json
    else:
        pending = PendingRegistration(
            email=registration_in.email, profile_data_json=profile_data_json
        )
        db.add(pending)

    def _is_already_registered_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "already registered" in message
            or "user already registered" in message
            or "already exists" in message
        )

    # Trigger Supabase user signup (sends confirmation email).
    try:
        import asyncio

        from libs.common.config import get_settings
        from libs.common.supabase import get_supabase_client, get_supabase_admin_client

        settings = get_settings()
        redirect_url = f"{settings.FRONTEND_URL.rstrip('/')}/confirm"
        supabase = get_supabase_client()

        if not registration_in.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password is required for signup.",
            )

        credentials = {
            "email": registration_in.email,
            "password": registration_in.password,
            "options": {
                "data": {
                    "first_name": registration_in.first_name,
                    "last_name": registration_in.last_name,
                },
                "email_redirect_to": redirect_url,
            },
        }

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(supabase.auth.sign_up, credentials)
                logger.info(
                    "User signed up in Supabase",
                    extra={"extra_fields": {"email": registration_in.email}},
                )

                # Set default app_metadata roles using service role
                try:
                    admin_supabase = get_supabase_admin_client()
                    user = getattr(response, "user", None)
                    user_id = getattr(user, "id", None) or (user or {}).get("id")
                    if user_id:
                        await asyncio.to_thread(
                            admin_supabase.auth.admin.update_user_by_id,
                            user_id,
                            {"app_metadata": {"roles": ["member"], "role": "member"}},
                        )
                        logger.info(
                            "Updated app_metadata roles",
                            extra={"extra_fields": {"user_id": user_id}},
                        )
                except Exception as meta_err:
                    logger.warning(
                        "Could not set app_metadata roles",
                        extra={"extra_fields": {"error": str(meta_err)}},
                    )

                last_error = None
                break
            except Exception as e:
                if _is_already_registered_error(e):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Email already registered. Please log in instead.",
                    ) from e

                last_error = e
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise e

        if last_error is not None:
            raise last_error

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(
            "Failed to create Supabase user",
            extra={"extra_fields": {"error": str(e), "email": registration_in.email}},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to create your authentication account right now. Please try again in a moment.",
        ) from e

    await db.commit()
    await db.refresh(pending)
    return pending


@router.delete("/by-email/{email}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pending_registration_by_email(
    email: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a pending registration by email (admin only)."""
    query = select(PendingRegistration).where(PendingRegistration.email == email)
    result = await db.execute(query)
    pending = result.scalar_one_or_none()
    if pending:
        await db.delete(pending)
        await db.commit()
    return None


@router.post(
    "/complete", response_model=MemberResponse, status_code=status.HTTP_200_OK
)
async def complete_pending_registration(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Complete a pending registration.
    Called after the user has verified their email and is authenticated.
    """
    # Idempotency: if member already exists, treat as success
    query = (
        select(Member)
        .where(Member.auth_id == current_user.user_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    existing_member = result.scalar_one_or_none()
    if existing_member:
        changed = await sync_member_roles(existing_member, current_user, db)
        if changed:
            normalize_member_tiers(existing_member)
            await db.commit()
            await db.refresh(existing_member)
        return existing_member

    # Find pending registration by email from token
    query = select(PendingRegistration).where(
        PendingRegistration.email == current_user.email
    )
    result = await db.execute(query)
    pending = result.scalar_one_or_none()

    if not pending:
        # Idempotency check for race condition
        query = (
            select(Member)
            .where(Member.auth_id == current_user.user_id)
            .options(*member_eager_load_options())
        )
        result = await db.execute(query)
        existing_member = result.scalar_one_or_none()
        if existing_member:
            changed = await sync_member_roles(existing_member, current_user, db)
            if changed:
                normalize_member_tiers(existing_member)
                await db.commit()
                await db.refresh(existing_member)
            return existing_member

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending registration not found",
        )

    # Create member from pending data
    profile_data = json.loads(pending.profile_data_json)

    roles = profile_data.get("roles") or ["member"]
    if isinstance(roles, list):
        roles = list(dict.fromkeys(roles)) or ["member"]
    else:
        roles = ["member"]

    # Create core Member record
    member = Member(
        auth_id=current_user.user_id,
        email=pending.email,
        first_name=profile_data.get("first_name"),
        last_name=profile_data.get("last_name"),
        registration_complete=True,
        approval_status="approved",
        roles=roles,
        profile_photo_url=profile_data.get("profile_photo_url"),
    )
    db.add(member)
    await db.flush()

    # Create MemberProfile sub-record
    member_profile = MemberProfile(
        member_id=member.id,
        phone=profile_data.get("phone"),
        city=profile_data.get("city"),
        country=profile_data.get("country"),
        time_zone=profile_data.get("time_zone"),
        gender=profile_data.get("gender"),
        date_of_birth=profile_data.get("date_of_birth"),
        occupation=profile_data.get("occupation"),
        area_in_lagos=profile_data.get("area_in_lagos"),
        swim_level=profile_data.get("swim_level"),
        deep_water_comfort=profile_data.get("deep_water_comfort"),
        strokes=profile_data.get("strokes"),
        interests=profile_data.get("interests"),
        personal_goals=profile_data.get("goals_narrative") or profile_data.get("personal_goals"),
        how_found_us=profile_data.get("how_found_us"),
        previous_communities=profile_data.get("previous_communities"),
        hopes_from_swimbuddz=profile_data.get("hopes_from_swimbuddz"),
        social_instagram=profile_data.get("social_instagram"),
        social_linkedin=profile_data.get("social_linkedin"),
        social_other=profile_data.get("social_other"),
        show_in_directory=profile_data.get("show_in_directory", False),
        interest_tags=profile_data.get("interest_tags"),
    )
    db.add(member_profile)

    # Create MemberEmergencyContact sub-record
    member_emergency = MemberEmergencyContact(
        member_id=member.id,
        name=profile_data.get("emergency_contact_name"),
        contact_relationship=profile_data.get("emergency_contact_relationship"),
        phone=profile_data.get("emergency_contact_phone"),
        region=profile_data.get("emergency_contact_region"),
        medical_info=profile_data.get("medical_info"),
        safety_notes=profile_data.get("safety_notes"),
    )
    db.add(member_emergency)

    # Create MemberAvailability sub-record
    preferred_locations = (
        profile_data.get("preferred_locations") or 
        profile_data.get("location_preference") or []
    )
    if profile_data.get("location_preference_other"):
        preferred_locations = list(preferred_locations) + [profile_data.get("location_preference_other")]
    
    accessible_facilities = (
        profile_data.get("accessible_facilities") or 
        profile_data.get("facility_access") or []
    )
    if profile_data.get("facility_access_other"):
        accessible_facilities = list(accessible_facilities) + [profile_data.get("facility_access_other")]
    
    equipment_needed = (
        profile_data.get("equipment_needed") or 
        profile_data.get("equipment_needs") or []
    )
    if profile_data.get("equipment_needs_other"):
        equipment_needed = list(equipment_needed) + [profile_data.get("equipment_needs_other")]

    member_availability = MemberAvailability(
        member_id=member.id,
        available_days=profile_data.get("available_days") or profile_data.get("availability_slots"),
        preferred_times=profile_data.get("preferred_times") or profile_data.get("time_of_day_availability"),
        preferred_locations=preferred_locations or None,
        accessible_facilities=accessible_facilities or None,
        travel_flexibility=profile_data.get("travel_flexibility"),
        equipment_needed=equipment_needed or None,
    )
    db.add(member_availability)

    # Create MemberMembership sub-record
    member_membership = MemberMembership(
        member_id=member.id,
        primary_tier=profile_data.get("primary_tier") or profile_data.get("membership_tier") or "community",
        active_tiers=profile_data.get("active_tiers") or profile_data.get("membership_tiers") or ["community"],
        requested_tiers=profile_data.get("requested_tiers") or profile_data.get("requested_membership_tiers"),
        club_badges_earned=profile_data.get("club_badges_earned", []),
        club_challenges_completed=profile_data.get("club_challenges_completed", {}),
        punctuality_score=profile_data.get("punctuality_score", 0),
        commitment_score=profile_data.get("commitment_score", 0),
        club_notes=profile_data.get("club_notes"),
        academy_skill_assessment=profile_data.get("academy_skill_assessment", {}),
        academy_goals=profile_data.get("academy_goals"),
        academy_preferred_coach_gender=profile_data.get("academy_preferred_coach_gender"),
        academy_lesson_preference=profile_data.get("academy_lesson_preference"),
        academy_certifications=profile_data.get("academy_certifications", []),
        academy_graduation_dates=profile_data.get("academy_graduation_dates", {}),
        academy_focus_areas=profile_data.get("academy_focus_areas"),
    )
    db.add(member_membership)

    # Create MemberPreferences sub-record
    member_preferences = MemberPreferences(
        member_id=member.id,
        language_preference=profile_data.get("language_preference"),
        comms_preference=profile_data.get("comms_preference"),
        payment_readiness=profile_data.get("payment_readiness"),
        currency_preference=profile_data.get("currency_preference"),
        consent_photo=profile_data.get("consent_photo"),
        community_rules_accepted=profile_data.get("community_rules_accepted", False),
        volunteer_interest=profile_data.get("volunteer_interest"),
        volunteer_roles_detail=profile_data.get("volunteer_roles_detail"),
        discovery_source=profile_data.get("discovery_source"),
    )
    db.add(member_preferences)

    await db.delete(pending)  # Cleanup pending

    try:
        await db.commit()
        await db.refresh(member)
    except Exception as e:
        error_str = str(e).lower()
        if "unique constraint" in error_str or "duplicate key" in error_str:
            await db.rollback()
            query = (
                select(Member)
                .where(Member.auth_id == current_user.user_id)
                .options(*member_eager_load_options())
            )
            result = await db.execute(query)
            existing_member = result.scalar_one_or_none()
            if existing_member:
                return existing_member

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Member already exists",
            )
        raise e

    # Reload with all relationships for response
    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()
