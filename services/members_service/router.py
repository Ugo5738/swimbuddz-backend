import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import Member, PendingRegistration
from services.members_service.schemas import (
    MemberResponse, 
    MemberCreate, 
    PendingRegistrationCreate, 
    PendingRegistrationResponse
)

router = APIRouter(prefix="/members", tags=["members"])
pending_router = APIRouter(prefix="/pending-registrations", tags=["pending-registrations"])


@pending_router.post("/", response_model=PendingRegistrationResponse, status_code=status.HTTP_201_CREATED)
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
    query = select(PendingRegistration).where(PendingRegistration.email == registration_in.email)
    result = await db.execute(query)
    pending = result.scalar_one_or_none()

    profile_data = registration_in.model_dump()
    profile_data_json = json.dumps(profile_data)

    if pending:
        pending.profile_data_json = profile_data_json
        # Update timestamp if we had one
    else:
        pending = PendingRegistration(
            email=registration_in.email,
            profile_data_json=profile_data_json
        )
        db.add(pending)

    await db.commit()
    await db.refresh(pending)
    return pending


@pending_router.post("/complete", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
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
    query = select(PendingRegistration).where(PendingRegistration.email == current_user.email)
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
        time_zone=profile_data.get("timeZone"),

        # Swim Profile
        swim_level=profile_data.get("swimLevel"),
        deep_water_comfort=profile_data.get("deepWaterComfort"),
        strokes=profile_data.get("strokes"),
        interests=profile_data.get("interests"),
        goals_narrative=profile_data.get("goalsNarrative"),
        goals_other=profile_data.get("goalsOther"),

        # Coaching
        certifications=profile_data.get("certifications"),
        coaching_experience=profile_data.get("coachingExperience"),
        coaching_specialties=profile_data.get("coachingSpecialties"),
        coaching_years=profile_data.get("coachingYears"),
        coaching_portfolio_link=profile_data.get("coachingPortfolioLink"),
        coaching_document_link=profile_data.get("coachingDocumentLink"),
        coaching_document_file_name=profile_data.get("coachingDocumentFileName"),

        # Logistics
        availability_slots=profile_data.get("availabilitySlots"),
        time_of_day_availability=profile_data.get("timeOfDayAvailability"),
        location_preference=profile_data.get("locationPreference"),
        location_preference_other=profile_data.get("locationPreferenceOther"),
        travel_flexibility=profile_data.get("travelFlexibility"),
        facility_access=profile_data.get("facilityAccess"),
        facility_access_other=profile_data.get("facilityAccessOther"),
        equipment_needs=profile_data.get("equipmentNeeds"),
        equipment_needs_other=profile_data.get("equipmentNeedsOther"),
        travel_notes=profile_data.get("travelNotes"),

        # Safety
        emergency_contact_name=profile_data.get("emergencyContactName"),
        emergency_contact_relationship=profile_data.get("emergencyContactRelationship"),
        emergency_contact_phone=profile_data.get("emergencyContactPhone"),
        emergency_contact_region=profile_data.get("emergencyContactRegion"),
        medical_info=profile_data.get("medicalInfo"),
        safety_notes=profile_data.get("safetyNotes"),

        # Community
        volunteer_interest=profile_data.get("volunteerInterest"),
        volunteer_roles_detail=profile_data.get("volunteerRolesDetail"),
        discovery_source=profile_data.get("discoverySource"),
        social_instagram=profile_data.get("socialInstagram"),
        social_linkedin=profile_data.get("socialLinkedIn"),
        social_other=profile_data.get("socialOther"),

        # Preferences
        language_preference=profile_data.get("languagePreference"),
        comms_preference=profile_data.get("commsPreference"),
        payment_readiness=profile_data.get("paymentReadiness"),
        currency_preference=profile_data.get("currencyPreference"),
        consent_photo=profile_data.get("consentPhoto"),

        # Membership
        membership_tiers=profile_data.get("membershipTiers"),
        academy_focus_areas=profile_data.get("academyFocusAreas"),
        academy_focus=profile_data.get("academyFocus"),
        payment_notes=profile_data.get("paymentNotes")
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
