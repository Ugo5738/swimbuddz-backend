import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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
    club_notes: Optional[str] = None

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
    requested_membership_tiers: Optional[list[str]] = None
    academy_focus_areas: Optional[list[str]] = None
    academy_focus: Optional[str] = None
    payment_notes: Optional[str] = None

    # ===== NEW TIER-BASED FIELDS =====
    # Tier Management
    membership_tier: Optional[str] = "community"

    # Profile Photo
    profile_photo_url: Optional[str] = None

    # ===== ABOUT YOU (Vetting Questions) =====
    occupation: Optional[str] = None
    area_in_lagos: Optional[str] = None
    how_found_us: Optional[str] = None
    previous_communities: Optional[str] = None
    hopes_from_swimbuddz: Optional[str] = None
    community_rules_accepted: Optional[bool] = False

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
    academy_paid_until: Optional[datetime] = None
    academy_alumni: Optional[bool] = False

    # Billing / Access
    community_paid_until: Optional[datetime] = None
    club_paid_until: Optional[datetime] = None


class CoachProfileResponse(BaseModel):
    id: uuid.UUID
    member_id: uuid.UUID

    # Identity
    display_name: Optional[str] = None
    coach_profile_photo_url: Optional[str] = None
    short_bio: Optional[str] = None
    full_bio: Optional[str] = None

    # Professional
    certifications: Optional[list[str]] = None
    other_certifications_note: Optional[str] = None

    coaching_years: Optional[int] = 0
    coaching_experience_summary: Optional[str] = None

    coaching_specialties: Optional[list[str]] = None
    levels_taught: Optional[list[str]] = None
    age_groups_taught: Optional[list[str]] = None
    preferred_cohort_types: Optional[list[str]] = None

    languages_spoken: Optional[list[str]] = None
    coaching_portfolio_link: Optional[str] = None

    # Safety
    has_cpr_training: Optional[bool] = False
    cpr_expiry_date: Optional[datetime] = None
    lifeguard_expiry_date: Optional[datetime] = None

    background_check_status: Optional[str] = None
    background_check_document_url: Optional[str] = None

    insurance_status: Optional[str] = None
    is_verified: bool

    # Logistics
    pools_supported: Optional[list[str]] = None
    can_travel_between_pools: bool
    travel_radius_km: Optional[float] = None

    max_swimmers_per_session: Optional[int] = 10
    max_cohorts_at_once: Optional[int] = 1

    accepts_one_to_one: bool
    accepts_group_cohorts: bool

    availability_notes: Optional[str] = None
    availability_calendar: Optional[dict] = None

    # Pricing
    currency: Optional[str] = "NGN"
    one_to_one_rate_per_hour: Optional[int] = None
    group_session_rate_per_hour: Optional[int] = None
    academy_cohort_stipend: Optional[int] = None

    # Platform
    status: str
    show_in_directory: bool
    is_featured: bool

    average_rating: float
    rating_count: int

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


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

    # Approval fields
    approval_status: Optional[str] = "pending"
    approval_notes: Optional[str] = None
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None

    # Coach Profile (if exists)
    coach_profile: Optional[CoachProfileResponse] = None

    model_config = ConfigDict(from_attributes=True)


class MemberListResponse(MemberResponse):
    is_coach: bool
    # Exclude full coach profile from list payloads to avoid extra queries
    coach_profile: Optional[CoachProfileResponse] = Field(default=None, exclude=True)


class MemberPublicResponse(BaseModel):
    id: uuid.UUID
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ActivateCommunityRequest(BaseModel):
    years: int = Field(default=1, ge=1, le=5)


class ActivateClubRequest(BaseModel):
    months: int = Field(default=1, ge=1, le=12)


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


# ===== APPROVAL SYSTEM SCHEMAS =====
class ApprovalAction(BaseModel):
    """Schema for approve/reject actions"""

    notes: Optional[str] = None  # Admin notes for the action


class PendingMemberResponse(MemberResponse):
    """Extended response for pending members (admin view)"""

    # Inherits all from MemberResponse, includes vetting fields
    pass
