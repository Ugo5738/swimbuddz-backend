import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.email import send_email
from libs.db.session import get_async_db
from services.members_service.models import (
    CoachProfile,
    Member,
    MemberChallengeCompletion,
    PendingRegistration,
    VolunteerInterest,
)
from services.members_service.schemas import (
    ActivateClubRequest,
    ActivateCommunityRequest,
    ApprovalAction,
    MemberCreate,
    MemberListResponse,
    MemberPublicResponse,
    MemberResponse,
    MemberUpdate,
    PendingMemberResponse,
    PendingRegistrationCreate,
    PendingRegistrationResponse,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/members", tags=["members"])
pending_router = APIRouter(
    prefix="/pending-registrations", tags=["pending-registrations"]
)
admin_router = APIRouter(prefix="/admin/members", tags=["admin-members"])


def _normalize_member_tiers(member: Member) -> bool:
    """
    Ensure membership_tiers and membership_tier reflect active entitlements.
    Returns True if a change was made.
    """
    now = datetime.now(timezone.utc)
    tier_priority = {"academy": 3, "club": 2, "community": 1}

    tiers = set(member.membership_tiers or [])
    if member.membership_tier:
        tiers.add(member.membership_tier)

    if member.club_paid_until and member.club_paid_until > now:
        tiers.update({"club", "community"})
    if member.community_paid_until and member.community_paid_until > now:
        tiers.add("community")

    if not tiers:
        tiers.add("community")

    sorted_tiers = sorted(
        [tier for tier in tiers if tier in tier_priority],
        key=lambda tier: tier_priority[tier],
        reverse=True,
    )

    changed = False
    if member.membership_tiers != sorted_tiers:
        member.membership_tiers = sorted_tiers
        changed = True

    top_tier = sorted_tiers[0] if sorted_tiers else None
    if top_tier and tier_priority.get(top_tier, 0) > tier_priority.get(
        member.membership_tier or "", 0
    ):
        member.membership_tier = top_tier
        changed = True

    return changed


@pending_router.post(
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
    # If this fails, fail the request so the frontend can prompt the user to retry.
    try:
        import asyncio

        from libs.common.config import get_settings

        from supabase import Client, create_client

        settings = get_settings()
        redirect_url = f"{settings.FRONTEND_URL.rstrip('/')}/confirm"
        # Use the anon key for signup to match Supabase's public signup flow.
        supabase: Client = create_client(
            settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY
        )

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
                print(f"User signed up in Supabase: {response}")
                last_error = None
                break
            except Exception as e:
                # If the email is already registered in Supabase Auth, prompt user to login instead.
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
        print(f"Failed to create Supabase user: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to create your authentication account right now. Please try again in a moment.",
        ) from e

    await db.commit()
    await db.refresh(pending)
    return pending


@pending_router.delete("/by-email/{email}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pending_registration_by_email(
    email: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete a pending registration by email (admin only).
    """
    query = select(PendingRegistration).where(PendingRegistration.email == email)
    result = await db.execute(query)
    pending = result.scalar_one_or_none()
    if pending:
        await db.delete(pending)
        await db.commit()
    return None


@pending_router.post(
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
    # Idempotency: if member already exists, treat as success.
    query = (
        select(Member)
        .where(Member.auth_id == current_user.user_id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    existing_member = result.scalar_one_or_none()
    if existing_member:
        return existing_member

    # Find pending registration by email from token
    query = select(PendingRegistration).where(
        PendingRegistration.email == current_user.email
    )
    result = await db.execute(query)
    pending = result.scalar_one_or_none()

    if not pending:
        # Idempotency: if pending is missing but member exists (race condition), treat as success.
        query = (
            select(Member)
            .where(Member.auth_id == current_user.user_id)
            .options(selectinload(Member.coach_profile))
        )
        result = await db.execute(query)
        existing_member = result.scalar_one_or_none()
        if existing_member:
            return existing_member

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending registration not found",
        )

    # Create member from pending data
    profile_data = json.loads(pending.profile_data_json)

    # Preserve roles from the pending payload so coach signups are identified correctly.
    roles = profile_data.get("roles") or ["member"]
    if isinstance(roles, list):
        roles = list(dict.fromkeys(roles)) or ["member"]  # de-dupe while keeping order
    else:
        roles = ["member"]

    member = Member(
        auth_id=current_user.user_id,
        email=pending.email,
        first_name=profile_data.get("first_name"),
        last_name=profile_data.get("last_name"),
        registration_complete=True,
        approval_status="approved",
        # Contact & Location
        phone=profile_data.get("phone"),
        area_in_lagos=profile_data.get("area_in_lagos"),
        city=profile_data.get("city"),
        country=profile_data.get("country"),
        time_zone=profile_data.get("time_zone"),
        # Swim Profile
        swim_level=profile_data.get("swim_level"),
        deep_water_comfort=profile_data.get("deep_water_comfort"),
        strokes=profile_data.get("strokes"),
        interests=profile_data.get("interests"),
        goals_narrative=profile_data.get("goals_narrative"),
        goals_other=profile_data.get("goals_other"),
        # Coaching
        certifications=profile_data.get("certifications"),
        coaching_experience=profile_data.get("coaching_experience"),
        coaching_specialties=profile_data.get("coaching_specialties"),
        coaching_years=profile_data.get("coaching_years"),
        coaching_portfolio_link=profile_data.get("coaching_portfolio_link"),
        coaching_document_link=profile_data.get("coaching_document_link"),
        coaching_document_file_name=profile_data.get("coaching_document_file_name"),
        # Logistics
        availability_slots=profile_data.get("availability_slots"),
        time_of_day_availability=profile_data.get("time_of_day_availability"),
        location_preference=profile_data.get("location_preference"),
        location_preference_other=profile_data.get("location_preference_other"),
        travel_flexibility=profile_data.get("travel_flexibility"),
        facility_access=profile_data.get("facility_access"),
        facility_access_other=profile_data.get("facility_access_other"),
        equipment_needs=profile_data.get("equipment_needs"),
        equipment_needs_other=profile_data.get("equipment_needs_other"),
        travel_notes=profile_data.get("travel_notes"),
        club_notes=profile_data.get("club_notes"),
        # Safety
        emergency_contact_name=profile_data.get("emergency_contact_name"),
        emergency_contact_relationship=profile_data.get(
            "emergency_contact_relationship"
        ),
        emergency_contact_phone=profile_data.get("emergency_contact_phone"),
        emergency_contact_region=profile_data.get("emergency_contact_region"),
        medical_info=profile_data.get("medical_info"),
        safety_notes=profile_data.get("safety_notes"),
        # Community
        volunteer_interest=profile_data.get("volunteer_interest"),
        volunteer_roles_detail=profile_data.get("volunteer_roles_detail"),
        discovery_source=profile_data.get("discovery_source"),
        social_instagram=profile_data.get("social_instagram"),
        social_linkedin=profile_data.get("social_linkedin"),
        social_other=profile_data.get("social_other"),
        # Preferences
        language_preference=profile_data.get("language_preference"),
        comms_preference=profile_data.get("comms_preference"),
        payment_readiness=profile_data.get("payment_readiness"),
        currency_preference=profile_data.get("currency_preference"),
        consent_photo=profile_data.get("consent_photo"),
        # Membership
        membership_tiers=profile_data.get("membership_tiers") or ["community"],
        requested_membership_tiers=profile_data.get("requested_membership_tiers"),
        academy_focus_areas=profile_data.get("academy_focus_areas"),
        academy_focus=profile_data.get("academy_focus"),
        payment_notes=profile_data.get("payment_notes"),
        roles=roles,
        # ===== NEW TIER-BASED FIELDS =====
        # Tier Management
        membership_tier=profile_data.get("membership_tier") or "community",
        # Profile Photo
        profile_photo_url=profile_data.get("profile_photo_url"),
        # Community Tier - Enhanced fields
        gender=profile_data.get("gender"),
        date_of_birth=profile_data.get("date_of_birth"),
        show_in_directory=profile_data.get("show_in_directory", False),
        interest_tags=profile_data.get("interest_tags", []),
        # Club Tier - Badges & Tracking (initialized as empty)
        club_badges_earned=profile_data.get("club_badges_earned", []),
        club_challenges_completed=profile_data.get("club_challenges_completed", {}),
        punctuality_score=profile_data.get("punctuality_score", 0),
        commitment_score=profile_data.get("commitment_score", 0),
        # Academy Tier - Skill Assessment & Goals
        academy_skill_assessment=profile_data.get("academy_skill_assessment", {}),
        academy_goals=profile_data.get("academy_goals"),
        academy_preferred_coach_gender=profile_data.get(
            "academy_preferred_coach_gender"
        ),
        academy_lesson_preference=profile_data.get("academy_lesson_preference"),
        academy_certifications=profile_data.get("academy_certifications", []),
        academy_graduation_dates=profile_data.get("academy_graduation_dates", {}),
    )

    db.add(member)
    await db.delete(pending)  # Cleanup pending

    try:
        await db.commit()
        await db.refresh(member)
    except Exception as e:
        # Handle race condition where member was created by another request
        # We check for "duplicate key value" or similar in the error string
        # or just catch generic IntegrityError if we imported it.
        # Since we didn't import IntegrityError yet, let's check the string
        # representation or import it. Better to import it.
        # But to be safe and quick without adding imports at top:
        error_str = str(e).lower()
        if "unique constraint" in error_str or "duplicate key" in error_str:
            await db.rollback()
            # Check if member exists now
            query = (
                select(Member)
                .where(Member.auth_id == current_user.user_id)
                .options(selectinload(Member.coach_profile))
            )
            result = await db.execute(query)
            existing_member = result.scalar_one_or_none()
            if existing_member:
                return existing_member

            # If not found but we had a unique error, raise 400
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Member already exists",
            )
        raise e

    # Avoid async lazy-loading issues during response serialization by returning an eagerly-loaded member.
    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.get("/coaches", response_model=List[MemberResponse])
async def list_coaches(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all active coaches.
    Returns Member objects that have an active CoachProfile.
    """
    query = (
        select(Member)
        .join(CoachProfile)
        .where(CoachProfile.status == "active")
        .options(selectinload(Member.coach_profile))
        .order_by(Member.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/me", response_model=MemberResponse)
async def get_current_member_profile(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get the profile of the currently authenticated member.
    """
    query = (
        select(Member)
        .where(Member.auth_id == current_user.user_id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )

    if _normalize_member_tiers(member):
        db.add(member)
        await db.commit()
        await db.refresh(member)

    return member


@router.post("/", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def create_member(
    member_in: MemberCreate,
    db: AsyncSession = Depends(get_async_db),
    # In a real app, we might restrict this to admin or internal services,
    # or ensure member_in.auth_id matches current_user.user_id
):
    """
    Directly create a member (internal use or admin).
    Normal users should go through the pending registration flow.
    """
    # Check if email already exists
    query = select(Member).where(Member.email == member_in.email)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    member = Member(**member_in.model_dump())
    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.get("/public", response_model=List[MemberPublicResponse])
async def list_public_members(
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all members for public dropdown (no auth required).
    Returns limited info (id, first_name, last_name).
    """
    query = select(Member).order_by(Member.first_name, Member.last_name)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/", response_model=List[MemberListResponse])
async def list_members(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all members (admin use).
    """
    query = (
        select(Member)
        .options(selectinload(Member.coach_profile))
        .offset(skip)
        .limit(limit)
        .order_by(Member.created_at.desc())
    )
    result = await db.execute(query)
    members = result.scalars().all()
    responses: list[MemberListResponse] = []
    for member in members:
        base = MemberResponse.model_validate(member, from_attributes=True)
        payload = base.model_dump(exclude={"coach_profile"})
        payload["is_coach"] = bool(member.coach_profile)
        responses.append(MemberListResponse(**payload))
    return responses


@router.get("/stats")
async def get_member_stats(
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get member statistics.
    """
    # Total Members
    query = select(func.count(Member.id))
    result = await db.execute(query)
    total_members = result.scalar_one() or 0

    # Active Members (registration complete)
    query = select(func.count(Member.id)).where(Member.registration_complete.is_(True))
    result = await db.execute(query)
    active_members = result.scalar_one() or 0

    # Approved Members
    query = select(func.count(Member.id)).where(Member.approval_status == "approved")
    result = await db.execute(query)
    approved_members = result.scalar_one() or 0

    # Pending Approvals
    query = select(func.count(Member.id)).where(Member.approval_status == "pending")
    result = await db.execute(query)
    pending_approvals = result.scalar_one() or 0

    return {
        "total_members": total_members,
        "active_members": active_members,
        "approved_members": approved_members,
        "pending_approvals": pending_approvals,
    }


@router.patch("/me", response_model=MemberResponse)
async def update_current_member(
    member_in: MemberUpdate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update the currently authenticated member's profile.
    """
    query = (
        select(Member)
        .where(Member.auth_id == current_user.user_id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found",
        )

    update_data = member_in.model_dump(exclude_unset=True)

    # Note: club_notes and travel_notes are distinct fields.

    # Prevent self-serve tampering with billing/entitlement fields.
    for protected_field in (
        "community_paid_until",
        "club_paid_until",
        "academy_paid_until",
        "academy_alumni",
    ):
        update_data.pop(protected_field, None)

    # Ignore no-op tier changes (same tier as current)
    if update_data.get("membership_tier") == member.membership_tier:
        update_data.pop("membership_tier", None)

    # Intercept membership_tiers update for non-admins (self-update)
    if "membership_tiers" in update_data:
        new_tiers = update_data.pop("membership_tiers")
        if new_tiers is not None:
            # Treat a request as a change only if the new tiers differ from current (including single-tier state)
            current_tiers = member.membership_tiers or (
                [member.membership_tier] if member.membership_tier else []
            )
            if set(new_tiers or []) != set(current_tiers):
                member.requested_membership_tiers = new_tiers
                # We could trigger a notification here

    for field, value in update_data.items():
        setattr(member, field, value)

    db.add(member)
    await db.commit()
    await db.refresh(member)
    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.post("/me/community/activate", response_model=MemberResponse)
async def activate_community_membership(
    payload: ActivateCommunityRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Deprecated: Community activation is owned by payments_service.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Community activation moved to payments. Create a payment intent via /api/v1/payments/intents.",
    )


@router.post("/me/club/activate", response_model=MemberResponse)
async def activate_club_membership(
    payload: ActivateClubRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Deprecated: Club activation is owned by payments_service.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Club activation moved to payments. Create a payment intent via /api/v1/payments/intents.",
    )


@router.get("/{member_id}", response_model=MemberResponse)
async def get_member(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a member by ID (admin use).
    """
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    return member


@admin_router.get("/by-email/{email}", response_model=MemberResponse)
async def get_member_by_email(
    email: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a member by email (admin only).
    """
    query = (
        select(Member)
        .where(func.lower(Member.email) == email.lower())
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    return member


@router.patch("/{member_id}", response_model=MemberResponse)
async def update_member(
    member_id: uuid.UUID,
    member_in: MemberUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update a member by ID (admin only).
    """
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    update_data = member_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(member, field, value)

    db.add(member)
    await db.commit()
    await db.refresh(member)
    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete a member by ID (admin only).
    """
    query = select(Member).where(Member.id == member_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    # Delete from Supabase Auth
    try:
        from libs.common.config import get_settings

        from supabase import Client, create_client

        settings = get_settings()
        supabase: Client = create_client(
            settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY
        )

        # auth_id is the Supabase User ID
        if member.auth_id:
            supabase.auth.admin.delete_user(member.auth_id)
            print(f"Deleted Supabase user: {member.auth_id}")

    except Exception as e:
        # Log error but proceed with local deletion to avoid getting stuck
        print(f"Failed to delete Supabase user {member.auth_id}: {e}")

    await db.execute(delete(CoachProfile).where(CoachProfile.member_id == member.id))
    await db.execute(
        delete(VolunteerInterest).where(VolunteerInterest.member_id == member.id)
    )
    await db.execute(
        delete(MemberChallengeCompletion).where(
            MemberChallengeCompletion.member_id == member.id
        )
    )

    await db.delete(member)
    await db.commit()
    return None


# ===== ADMIN APPROVAL ENDPOINTS =====
@admin_router.get("/pending", response_model=List[PendingMemberResponse])
async def list_pending_members(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all members with pending approval status (admin only).
    """
    query = (
        select(Member)
        .where(Member.approval_status == "pending")
        .options(selectinload(Member.coach_profile))
        .order_by(Member.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@admin_router.post("/{member_id}/approve", response_model=MemberResponse)
async def approve_member(
    member_id: uuid.UUID,
    action: ApprovalAction,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Approve a pending member registration (admin only).
    """
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    if member.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Member is already {member.approval_status}",
        )

    member.approval_status = "approved"
    member.approved_at = datetime.now(timezone.utc)
    member.approved_by = current_user.email
    if action.notes:
        member.approval_notes = action.notes

    db.add(member)
    await db.commit()
    await db.refresh(member)

    # Send approval email notification
    await send_email(
        to_email=member.email,
        subject="Welcome to SwimBuddz! Your account is approved",
        body=(
            f"Hi {member.first_name},\n\n"
            "Congratulations! Your SwimBuddz membership application has been "
            "approved.\n\n"
            "You can now log in and access all member features.\n\n"
            "Welcome to the community!\n"
            "The SwimBuddz Team"
        ),
    )

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    return result.scalar_one()


@admin_router.post("/{member_id}/reject", response_model=MemberResponse)
async def reject_member(
    member_id: uuid.UUID,
    action: ApprovalAction,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Reject a pending member registration (admin only).
    User can reapply later.
    """
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    if member.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Member is already {member.approval_status}",
        )

    member.approval_status = "rejected"
    member.approved_at = datetime.now(timezone.utc)
    member.approved_by = current_user.email
    if action.notes:
        member.approval_notes = action.notes

    db.add(member)
    await db.commit()
    await db.refresh(member)

    # Send rejection email notification
    await send_email(
        to_email=member.email,
        subject="Update on your SwimBuddz application",
        body=(
            f"Hi {member.first_name},\n\n"
            "Thank you for your interest in SwimBuddz.\n\n"
            "After reviewing your application, we are unable to approve your "
            "membership at this time.\n\n"
            f"Reason: {action.notes or 'Does not meet current criteria'}\n\n"
            "You are welcome to reapply in the future.\n\n"
            "Best regards,\n"
            "The SwimBuddz Team"
        ),
    )

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    return result.scalar_one()


@admin_router.post("/{member_id}/approve-upgrade", response_model=MemberResponse)
async def approve_member_upgrade(
    member_id: uuid.UUID,
    action: ApprovalAction,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Approve a pending tier upgrade for an already-approved member.
    Moves requested tiers into active tiers and clears the request flag.
    """
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    if not member.requested_membership_tiers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No upgrade request pending for this member",
        )

    new_tiers = member.requested_membership_tiers or []

    member.membership_tiers = new_tiers
    if new_tiers:
        member.membership_tier = new_tiers[0]

    # Clear the pending upgrade request
    member.requested_membership_tiers = None

    # Track admin action
    member.approved_by = current_user.email
    member.approved_at = datetime.now(timezone.utc)
    if action.notes:
        member.approval_notes = action.notes

    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    return result.scalar_one()


@admin_router.post(
    "/by-auth/{auth_id}/community/activate", response_model=MemberResponse
)
async def admin_activate_community_membership_by_auth(
    auth_id: str,
    payload: ActivateCommunityRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Apply Community entitlement for a member (admin/service use, e.g. payment webhook).
    """
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    now = datetime.now(timezone.utc)
    base = (
        member.community_paid_until
        if member.community_paid_until and member.community_paid_until > now
        else now
    )
    member.community_paid_until = base + timedelta(days=365 * payload.years)

    if not member.membership_tiers:
        member.membership_tiers = ["community"]
    if not member.membership_tier:
        member.membership_tier = "community"

    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    return result.scalar_one()


@admin_router.post("/by-auth/{auth_id}/club/activate", response_model=MemberResponse)
async def admin_activate_club_membership_by_auth(
    auth_id: str,
    payload: ActivateClubRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Apply Club entitlement for a member (admin/service use, e.g. payment webhook).
    """
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    now = datetime.now(timezone.utc)
    if not (member.community_paid_until and member.community_paid_until > now):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Community membership is not active for this member",
        )

    approved_tiers = set(
        (member.membership_tiers or [])
        + ([member.membership_tier] if member.membership_tier else [])
    )
    requested_tiers = set(member.requested_membership_tiers or [])
    club_approved = "club" in approved_tiers or "academy" in approved_tiers
    club_requested = "club" in requested_tiers or "academy" in requested_tiers
    readiness_complete = bool(
        member.emergency_contact_name
        and member.emergency_contact_relationship
        and member.emergency_contact_phone
        and member.location_preference
        and len(member.location_preference) > 0
        and member.time_of_day_availability
        and len(member.time_of_day_availability) > 0
        and member.availability_slots
        and len(member.availability_slots) > 0
    )

    if not club_approved:
        if not club_requested:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Club upgrade not requested",
            )
        if not readiness_complete:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Club readiness is incomplete",
            )

    tier_priority = {"academy": 3, "club": 2, "community": 1}

    base = (
        member.club_paid_until
        if member.club_paid_until and member.club_paid_until > now
        else now
    )
    member.club_paid_until = base + timedelta(days=30 * payload.months)

    updated_tiers = set(approved_tiers)
    updated_tiers.update({"club", "community"})

    if not club_approved:
        if member.requested_membership_tiers:
            remaining_requests = [
                tier
                for tier in member.requested_membership_tiers
                if tier not in {"club", "community"}
            ]
            member.requested_membership_tiers = remaining_requests or None
    elif member.requested_membership_tiers:
        # Clear stale club/academy requests now that approval is in place.
        remaining_requests = [
            tier
            for tier in member.requested_membership_tiers
            if tier not in {"club", "academy", "community"}
        ]
        member.requested_membership_tiers = remaining_requests or None

    sorted_tiers = sorted(
        [tier for tier in updated_tiers if tier in tier_priority],
        key=lambda tier: tier_priority[tier],
        reverse=True,
    )
    if sorted_tiers:
        member.membership_tiers = sorted_tiers
        current_priority = tier_priority.get(member.membership_tier or "", 0)
        top_priority = tier_priority.get(sorted_tiers[0], 0)
        if top_priority > current_priority:
            member.membership_tier = sorted_tiers[0]

    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(selectinload(Member.coach_profile))
    )
    result = await db.execute(query)
    return result.scalar_one()
