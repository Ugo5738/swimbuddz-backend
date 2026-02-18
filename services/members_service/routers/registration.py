"""Pending registration router - handles user registration flow."""

import asyncio
import json
import re

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post
from libs.common.supabase import get_supabase_admin_client
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
from services.members_service.routers._helpers import (
    member_eager_load_options,
    normalize_member_tiers,
    sync_member_roles,
)
from services.members_service.schemas import (
    MemberResponse,
    PendingRegistrationCreate,
    PendingRegistrationResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)
router = APIRouter(prefix="/pending-registrations", tags=["pending-registrations"])
settings = get_settings()


async def _ensure_wallet_exists(member_id: str, member_auth_id: str) -> None:
    """Best-effort wallet auto-provisioning on registration completion."""
    try:
        resp = await internal_post(
            service_url=settings.WALLET_SERVICE_URL,
            path="/internal/wallet/create",
            calling_service="members",
            json={
                "member_id": member_id,
                "member_auth_id": member_auth_id,
            },
            timeout=15.0,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Wallet auto-create failed for member_auth_id=%s (http %d): %s",
                member_auth_id,
                resp.status_code,
                resp.text,
            )
    except Exception as exc:
        logger.warning(
            "Wallet auto-create request failed for member_auth_id=%s: %s",
            member_auth_id,
            exc,
        )


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
        from libs.common.supabase import get_supabase_admin_client, get_supabase_client

        settings = get_settings()
        redirect_url = f"{settings.FRONTEND_URL.rstrip('/')}/confirm"
        supabase = get_supabase_client()

        if not registration_in.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password is required for signup.",
            )

        requested_tiers = (
            registration_in.requested_membership_tiers
            if hasattr(registration_in, "requested_membership_tiers")
            else None
        )
        requested_tiers = requested_tiers or registration_in.model_dump().get(
            "requested_membership_tiers"
        )
        if requested_tiers is not None and not isinstance(requested_tiers, list):
            requested_tiers = None

        credentials = {
            "email": registration_in.email,
            "password": registration_in.password,
            "options": {
                "data": {
                    "first_name": registration_in.first_name,
                    "last_name": registration_in.last_name,
                    # Preserve tier intent in auth metadata for recovery flows.
                    "requested_membership_tiers": requested_tiers,
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

                # Set app_metadata roles using service role
                # Preserve roles from the registration payload (e.g. ["coach"])
                try:
                    admin_supabase = get_supabase_admin_client()
                    user = getattr(response, "user", None)
                    user_id = getattr(user, "id", None) or (user or {}).get("id")
                    if user_id:
                        # Read roles from registration payload; default to ["member"]
                        requested_roles = registration_in.model_dump().get("roles")
                        if isinstance(requested_roles, list) and requested_roles:
                            # Ensure "member" is always included alongside any other roles
                            initial_roles = list(
                                dict.fromkeys(["member"] + requested_roles)
                            )
                        else:
                            initial_roles = ["member"]
                        await asyncio.to_thread(
                            admin_supabase.auth.admin.update_user_by_id,
                            user_id,
                            {
                                "app_metadata": {
                                    "roles": initial_roles,
                                    "role": initial_roles[0],
                                }
                            },
                        )
                        logger.info(
                            "Updated app_metadata roles",
                            extra={
                                "extra_fields": {
                                    "user_id": user_id,
                                    "roles": initial_roles,
                                }
                            },
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


@router.post("/complete", response_model=MemberResponse, status_code=status.HTTP_200_OK)
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
        await _ensure_wallet_exists(str(existing_member.id), existing_member.auth_id)
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
            await _ensure_wallet_exists(
                str(existing_member.id), existing_member.auth_id
            )
            changed = await sync_member_roles(existing_member, current_user, db)
            if changed:
                normalize_member_tiers(existing_member)
                await db.commit()
                await db.refresh(existing_member)
            return existing_member

        def _fallback_profile_from_auth(user: AuthUser) -> dict:
            meta = user.user_metadata or {}
            first_name = (
                meta.get("first_name")
                or meta.get("firstName")
                or meta.get("given_name")
            )
            last_name = (
                meta.get("last_name") or meta.get("lastName") or meta.get("family_name")
            )

            email = user.email or ""
            if (not first_name or not last_name) and email:
                local_part = email.split("@")[0]
                tokens = [t for t in re.split(r"[._-]+", local_part) if t]
                if not first_name and tokens:
                    first_name = tokens[0].capitalize()
                if not last_name:
                    last_name = tokens[1].capitalize() if len(tokens) > 1 else "Member"

            if not first_name:
                first_name = "Member"
            if not last_name:
                last_name = "User"

            raw_roles = []
            meta_roles = meta.get("roles")
            if isinstance(meta_roles, list):
                raw_roles.extend(meta_roles)
            elif isinstance(meta_roles, str):
                raw_roles.append(meta_roles)
            raw_roles.extend(user.roles or [])

            allowed = {"member", "coach", "admin"}
            normalized_roles = []
            for role in raw_roles:
                if not isinstance(role, str):
                    continue
                role_value = role.strip().lower()
                if role_value in allowed and role_value not in normalized_roles:
                    normalized_roles.append(role_value)

            if not normalized_roles:
                normalized_roles = ["member"]

            raw_requested = meta.get("requested_membership_tiers")
            if isinstance(raw_requested, list):
                requested_tiers = [str(t).lower() for t in raw_requested if t]
            elif isinstance(raw_requested, str):
                requested_tiers = [raw_requested.lower()]
            else:
                requested_tiers = []

            valid_tiers = {"community", "club", "academy"}
            requested_tiers = [t for t in requested_tiers if t in valid_tiers]

            return {
                "first_name": first_name,
                "last_name": last_name,
                "roles": normalized_roles,
                "membership_tier": "community",
                "membership_tiers": ["community"],
                "requested_membership_tiers": requested_tiers,
                "community_rules_accepted": True,
            }

        profile_data = _fallback_profile_from_auth(current_user)
        pending_email = current_user.email
        if not pending_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email missing from auth token; cannot complete registration.",
            )

        logger.warning(
            "Pending registration not found; creating member from auth metadata",
            extra={"extra_fields": {"email": pending_email}},
        )
    else:
        pending_email = pending.email
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
        email=pending_email,
        first_name=profile_data.get("first_name"),
        last_name=profile_data.get("last_name"),
        registration_complete=True,
        approval_status="approved",
        roles=roles,
        profile_photo_media_id=profile_data.get("profile_photo_media_id"),
    )
    db.add(member)
    await db.flush()

    # Create MemberProfile sub-record
    member_profile = MemberProfile(
        member_id=member.id,
        phone=profile_data.get("phone"),
        address=profile_data.get("address") or profile_data.get("area_in_lagos"),
        city=profile_data.get("city"),
        state=profile_data.get("state"),
        country=profile_data.get("country"),
        time_zone=profile_data.get("time_zone"),
        gender=profile_data.get("gender"),
        date_of_birth=profile_data.get("date_of_birth"),
        occupation=profile_data.get("occupation"),
        area_in_lagos=profile_data.get("area_in_lagos") or profile_data.get("address"),
        swim_level=profile_data.get("swim_level"),
        deep_water_comfort=profile_data.get("deep_water_comfort"),
        strokes=profile_data.get("strokes"),
        interests=profile_data.get("interests"),
        personal_goals=profile_data.get("goals_narrative")
        or profile_data.get("personal_goals"),
        how_found_us=profile_data.get("how_found_us"),
        previous_communities=profile_data.get("previous_communities"),
        hopes_from_swimbuddz=profile_data.get("hopes_from_swimbuddz"),
        social_instagram=profile_data.get("social_instagram"),
        social_linkedin=profile_data.get("social_linkedin"),
        social_other=profile_data.get("social_other"),
        show_in_directory=profile_data.get("show_in_directory", True),
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
        profile_data.get("preferred_locations")
        or profile_data.get("location_preference")
        or []
    )
    if profile_data.get("location_preference_other"):
        preferred_locations = list(preferred_locations) + [
            profile_data.get("location_preference_other")
        ]

    accessible_facilities = (
        profile_data.get("accessible_facilities")
        or profile_data.get("facility_access")
        or []
    )
    if profile_data.get("facility_access_other"):
        accessible_facilities = list(accessible_facilities) + [
            profile_data.get("facility_access_other")
        ]

    equipment_needed = (
        profile_data.get("equipment_needed")
        or profile_data.get("equipment_needs")
        or []
    )
    if profile_data.get("equipment_needs_other"):
        equipment_needed = list(equipment_needed) + [
            profile_data.get("equipment_needs_other")
        ]

    member_availability = MemberAvailability(
        member_id=member.id,
        available_days=profile_data.get("available_days")
        or profile_data.get("availability_slots"),
        preferred_times=profile_data.get("preferred_times")
        or profile_data.get("time_of_day_availability"),
        preferred_locations=preferred_locations or None,
        accessible_facilities=accessible_facilities or None,
        travel_flexibility=profile_data.get("travel_flexibility"),
        equipment_needed=equipment_needed or None,
    )
    db.add(member_availability)

    # Create MemberMembership sub-record
    member_membership = MemberMembership(
        member_id=member.id,
        primary_tier=profile_data.get("primary_tier")
        or profile_data.get("membership_tier")
        or "community",
        active_tiers=profile_data.get("active_tiers")
        or profile_data.get("membership_tiers")
        or ["community"],
        requested_tiers=profile_data.get("requested_tiers")
        or profile_data.get("requested_membership_tiers"),
        club_badges_earned=profile_data.get("club_badges_earned", []),
        club_challenges_completed=profile_data.get("club_challenges_completed", {}),
        punctuality_score=profile_data.get("punctuality_score", 0),
        commitment_score=profile_data.get("commitment_score", 0),
        club_notes=profile_data.get("club_notes"),
        academy_skill_assessment=profile_data.get("academy_skill_assessment", {}),
        academy_goals=profile_data.get("academy_goals"),
        academy_preferred_coach_gender=profile_data.get(
            "academy_preferred_coach_gender"
        ),
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

    if pending:
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
                await _ensure_wallet_exists(
                    str(existing_member.id), existing_member.auth_id
                )
                return existing_member

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Member already exists",
            )
        raise e

    await _ensure_wallet_exists(str(member.id), member.auth_id)

    # Sync member roles to Supabase app_metadata so JWT reflects them
    # This ensures roles like "coach" set during registration are in the token
    if member.roles and set(member.roles) != {"member"}:
        try:
            admin_supabase = get_supabase_admin_client()
            final_roles = list(dict.fromkeys(["member"] + (member.roles or [])))
            await asyncio.to_thread(
                admin_supabase.auth.admin.update_user_by_id,
                current_user.user_id,
                {"app_metadata": {"roles": final_roles}},
            )
            logger.info(
                "Synced member roles to Supabase on registration completion",
                extra={
                    "extra_fields": {
                        "user_id": current_user.user_id,
                        "roles": final_roles,
                    }
                },
            )
        except Exception as sync_err:
            logger.warning(
                "Could not sync member roles to Supabase",
                extra={"extra_fields": {"error": str(sync_err)}},
            )

    # Reload with all relationships for response
    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()
