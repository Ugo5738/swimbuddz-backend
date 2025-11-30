import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, ConfigDict


class MemberBase(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str

    # Contact & Location
    phone: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    time_zone: Optional[str] = None

    # Swim Profile
    swim_level: Optional[str] = None
    deep_water_comfort: Optional[str] = None
    strokes: Optional[list[str]] = None
    interests: Optional[list[str]] = None
    goals_narrative: Optional[str] = None
    goals_other: Optional[str] = None

    # Coaching
    certifications: Optional[list[str]] = None
    coaching_experience: Optional[str] = None
    coaching_specialties: Optional[list[str]] = None
    coaching_years: Optional[str] = None
    coaching_portfolio_link: Optional[str] = None
    coaching_document_link: Optional[str] = None
    coaching_document_file_name: Optional[str] = None

    # Logistics
    availability_slots: Optional[list[str]] = None
    time_of_day_availability: Optional[list[str]] = None
    location_preference: Optional[list[str]] = None
    location_preference_other: Optional[str] = None
    travel_flexibility: Optional[str] = None
    facility_access: Optional[list[str]] = None
    facility_access_other: Optional[str] = None
    equipment_needs: Optional[list[str]] = None
    equipment_needs_other: Optional[str] = None
    travel_notes: Optional[str] = None

    # Safety
    emergency_contact_name: Optional[str] = None
    emergency_contact_relationship: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_region: Optional[str] = None
    medical_info: Optional[str] = None
    safety_notes: Optional[str] = None

    # Community
    volunteer_interest: Optional[list[str]] = None
    volunteer_roles_detail: Optional[str] = None
    discovery_source: Optional[str] = None
    social_instagram: Optional[str] = None
    social_linkedin: Optional[str] = None
    social_other: Optional[str] = None

    # Preferences
    language_preference: Optional[str] = None
    comms_preference: Optional[str] = None
    payment_readiness: Optional[str] = None
    currency_preference: Optional[str] = None
    consent_photo: Optional[str] = None

    # Membership
    membership_tiers: Optional[list[str]] = None
    academy_focus_areas: Optional[list[str]] = None
    academy_focus: Optional[str] = None
    payment_notes: Optional[str] = None

    # ===== NEW TIER-BASED FIELDS =====
    # Tier Management
    membership_tier: Optional[str] = "community"

    # Profile Photo
    profile_photo_url: Optional[str] = None

    # Community Tier - Enhanced fields
    gender: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    show_in_directory: Optional[bool] = False
    interest_tags: Optional[list[str]] = None

    # Club Tier - Badges & Tracking
    club_badges_earned: Optional[list[str]] = None
    club_challenges_completed: Optional[dict] = None
    punctuality_score: Optional[int] = 0
    commitment_score: Optional[int] = 0

    # Academy Tier - Skill Assessment & Goals
    academy_skill_assessment: Optional[dict] = None
    academy_goals: Optional[str] = None
    academy_preferred_coach_gender: Optional[str] = None
    academy_lesson_preference: Optional[str] = None
    academy_certifications: Optional[list[str]] = None
    academy_graduation_dates: Optional[dict] = None


class MemberCreate(MemberBase):
    auth_id: str


class MemberUpdate(MemberBase):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None

    # All other fields are inherited from MemberBase as Optional
    # We just need to ensure we can update them.
    # MemberBase fields are already Optional, so this works.
    pass


class MemberResponse(MemberBase):
    id: uuid.UUID
    auth_id: str
    is_active: bool
    registration_complete: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PendingRegistrationCreate(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    password: Optional[str] = None
    # Add other profile fields as needed, for now just these
    model_config = ConfigDict(extra="allow")


class PendingRegistrationResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
