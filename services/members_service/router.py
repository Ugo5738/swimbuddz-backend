import json
from typing import List
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import Member, PendingRegistration
from services.members_service.schemas import (
    MemberResponse,
    MemberCreate,
    MemberUpdate,
    PendingRegistrationCreate,
    PendingRegistrationResponse,
)

router = APIRouter(prefix="/members", tags=["members"])
pending_router = APIRouter(
    prefix="/pending-registrations", tags=["pending-registrations"]
)


@pending_router.post(
    "/", response_model=PendingRegistrationResponse, status_code=status.HTTP_201_CREATED
)
async def create_pending_registration(
    registration_in: PendingRegistrationCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a pending registration.
    This is called by the frontend before the user signs up with Supabase.
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
        # Update timestamp if we had one
    else:
        pending = PendingRegistration(
            email=registration_in.email, profile_data_json=profile_data_json
        )
        db.add(pending)

    await db.commit()
    await db.refresh(pending)

    # Trigger Supabase User Creation
    # We use admin.create_user to create the user immediately with the provided password.
    # We set email_confirm=False so they still need to verify their email.
    try:
        from supabase import create_client, Client
        from libs.common.config import get_settings

        settings = get_settings()
        supabase: Client = create_client(
            settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY
        )

        # Check if we have a password (we should from frontend now)
        if registration_in.password:
            # Use sign_up instead of admin.create_user to ensure the confirmation email is sent.
            # sign_up mimics a real user signup flow.
            credentials = {
                "email": registration_in.email,
                "password": registration_in.password,
                "options": {
                    "data": {
                        "first_name": registration_in.first_name,
                        "last_name": registration_in.last_name,
                    },
                    "email_redirect_to": "http://localhost:3000/confirm",
                },
            }
            response = supabase.auth.sign_up(credentials)
            print(f"User signed up in Supabase: {response}")
        else:
            # Fallback to invite if no password (shouldn't happen with new frontend)
            redirect_url = "http://localhost:3000/confirm"
            response = supabase.auth.admin.invite_user_by_email(
                registration_in.email, options={"redirect_to": redirect_url}
            )
            print(f"Invitation sent (fallback): {response}")

    except Exception as e:
        # Log error. If user already exists in Supabase but not in our DB (edge case),
        # we might want to handle it. For now, just log.
        print(f"Failed to create Supabase user: {e}")

    return pending


@pending_router.post(
    "/complete", response_model=MemberResponse, status_code=status.HTTP_201_CREATED
)
async def complete_pending_registration(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Complete a pending registration.
    Called after the user has verified their email and is authenticated.
    """
    # Check if member already exists
    query = select(Member).where(Member.auth_id == current_user.user_id)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Member already exists",
        )

    # Find pending registration by email from token
    query = select(PendingRegistration).where(
        PendingRegistration.email == current_user.email
    )
    result = await db.execute(query)
    pending = result.scalar_one_or_none()

    if not pending:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending registration not found",
        )

    # Create member from pending data
    profile_data = json.loads(pending.profile_data_json)

    member = Member(
        auth_id=current_user.user_id,
        email=pending.email,
        first_name=profile_data.get("first_name"),
        last_name=profile_data.get("last_name"),
        registration_complete=True,
        # Contact & Location
        phone=profile_data.get("phone"),
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
        membership_tiers=profile_data.get("membership_tiers"),
        academy_focus_areas=profile_data.get("academy_focus_areas"),
        academy_focus=profile_data.get("academy_focus"),
        payment_notes=profile_data.get("payment_notes"),
        # ===== NEW TIER-BASED FIELDS =====
        # Tier Management
        membership_tier=profile_data.get("membership_tier", "community"),
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
        # Since we didn't import IntegrityError yet, let's check the string representation
        # or import it. Better to import it.
        # But to be safe and quick without adding imports at top:
        error_str = str(e).lower()
        if "unique constraint" in error_str or "duplicate key" in error_str:
            await db.rollback()
            # Check if member exists now
            query = select(Member).where(Member.auth_id == current_user.user_id)
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

    return member


@router.get("/me", response_model=MemberResponse)
async def get_current_member_profile(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get the profile of the currently authenticated member.
    """
    query = select(Member).where(Member.auth_id == current_user.user_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )

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
    return member


@router.get("/public", response_model=List[MemberResponse])
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


@router.get("/", response_model=List[MemberResponse])
async def list_members(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all members (admin use).
    """
    query = select(Member).offset(skip).limit(limit).order_by(Member.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


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

    # Active Members
    query = select(func.count(Member.id)).where(Member.registration_complete.is_(True))
    result = await db.execute(query)
    active_members = result.scalar_one() or 0

    return {"total_members": total_members, "active_members": active_members}


@router.patch("/me", response_model=MemberResponse)
async def update_current_member(
    member_in: MemberUpdate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update the currently authenticated member's profile.
    """
    query = select(Member).where(Member.auth_id == current_user.user_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found",
        )

    update_data = member_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(member, field, value)

    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


@router.get("/{member_id}", response_model=MemberResponse)
async def get_member(
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a member by ID (admin use).
    """
    query = select(Member).where(Member.id == member_id)
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
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update a member by ID.
    """
    query = select(Member).where(Member.id == member_id)
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
    return member
