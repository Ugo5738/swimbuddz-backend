"""Pydantic schemas for member service with nested structure.

The schemas mirror the decomposed Member model:
- MemberResponse: Core identity + nested sub-responses
- MemberProfileResponse: Personal info, swim profile
- MemberEmergencyContactResponse: Emergency contact, medical
- MemberAvailabilityResponse: Scheduling preferences
- MemberMembershipResponse: Tiers, billing, gamification
- MemberPreferencesResponse: User settings
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ============================================================================
# SUB-TABLE RESPONSE SCHEMAS
# ============================================================================


class MemberProfileResponse(BaseModel):
    """Personal info, swim profile, and social links."""

    id: uuid.UUID
    member_id: uuid.UUID

    # Contact
    phone: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    time_zone: Optional[str] = None

    # Demographics
    gender: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    occupation: Optional[str] = None
    area_in_lagos: Optional[str] = None

    # Swim Profile
    swim_level: Optional[str] = None
    deep_water_comfort: Optional[str] = None
    strokes: Optional[list[str]] = None
    interests: Optional[list[str]] = None
    personal_goals: Optional[str] = None

    # Discovery
    how_found_us: Optional[str] = None
    previous_communities: Optional[str] = None
    hopes_from_swimbuddz: Optional[str] = None

    # Social
    social_instagram: Optional[str] = None
    social_linkedin: Optional[str] = None
    social_other: Optional[str] = None

    # Directory
    show_in_directory: bool = False
    interest_tags: Optional[list[str]] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MemberEmergencyContactResponse(BaseModel):
    """Emergency contact and medical information."""

    id: uuid.UUID
    member_id: uuid.UUID

    name: Optional[str] = None
    contact_relationship: Optional[str] = None
    phone: Optional[str] = None
    region: Optional[str] = None
    medical_info: Optional[str] = None
    safety_notes: Optional[str] = None

    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MemberAvailabilityResponse(BaseModel):
    """Scheduling and location preferences."""

    id: uuid.UUID
    member_id: uuid.UUID

    available_days: Optional[list[str]] = None
    preferred_times: Optional[list[str]] = None
    preferred_locations: Optional[list[str]] = None
    accessible_facilities: Optional[list[str]] = None
    travel_flexibility: Optional[str] = None
    equipment_needed: Optional[list[str]] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MemberMembershipResponse(BaseModel):
    """Membership tiers, billing, and gamification."""

    id: uuid.UUID
    member_id: uuid.UUID

    # Tiers
    primary_tier: str = "community"
    active_tiers: Optional[list[str]] = None
    requested_tiers: Optional[list[str]] = None

    # Billing
    community_paid_until: Optional[datetime] = None
    club_paid_until: Optional[datetime] = None
    academy_paid_until: Optional[datetime] = None
    pending_payment_reference: Optional[str] = None

    # Club Gamification
    club_badges_earned: Optional[list[str]] = None
    club_challenges_completed: Optional[dict] = None
    punctuality_score: int = 0
    commitment_score: int = 0
    club_notes: Optional[str] = None

    # Academy
    academy_skill_assessment: Optional[dict] = None
    academy_goals: Optional[str] = None
    academy_preferred_coach_gender: Optional[str] = None
    academy_lesson_preference: Optional[str] = None
    academy_certifications: Optional[list[str]] = None
    academy_graduation_dates: Optional[dict] = None
    academy_alumni: bool = (
        False  # DEPRECATED: Use Enrollment.status == GRADUATED instead
    )
    academy_focus_areas: Optional[list[str]] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MemberPreferencesResponse(BaseModel):
    """User settings and preferences."""

    id: uuid.UUID
    member_id: uuid.UUID

    language_preference: Optional[str] = None
    comms_preference: Optional[str] = None
    payment_readiness: Optional[str] = None
    currency_preference: Optional[str] = None
    consent_photo: Optional[str] = None
    community_rules_accepted: bool = False

    volunteer_interest: Optional[list[str]] = None
    volunteer_roles_detail: Optional[str] = None
    discovery_source: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# COACH PROFILE SCHEMA
# ============================================================================


class CoachProfileResponse(BaseModel):
    """Coach-specific profile data."""

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
    coaching_years: int = 0
    coaching_experience_summary: Optional[str] = None
    coaching_specialties: Optional[list[str]] = None
    levels_taught: Optional[list[str]] = None
    age_groups_taught: Optional[list[str]] = None
    preferred_cohort_types: Optional[list[str]] = None
    languages_spoken: Optional[list[str]] = None
    coaching_portfolio_link: Optional[str] = None
    coaching_document_link: Optional[str] = None
    coaching_document_file_name: Optional[str] = None

    # Safety
    has_cpr_training: bool = False
    cpr_expiry_date: Optional[datetime] = None
    lifeguard_expiry_date: Optional[datetime] = None
    background_check_status: Optional[str] = None
    background_check_document_url: Optional[str] = None
    insurance_status: Optional[str] = None
    is_verified: bool = False

    # Logistics
    pools_supported: Optional[list[str]] = None
    can_travel_between_pools: bool = False
    travel_radius_km: Optional[float] = None
    max_swimmers_per_session: int = 10
    max_cohorts_at_once: int = 1
    accepts_one_on_one: bool = True
    accepts_group_cohorts: bool = True
    availability_calendar: Optional[dict] = None

    # Pricing
    currency: str = "NGN"
    one_to_one_rate_per_hour: Optional[int] = None
    group_session_rate_per_hour: Optional[int] = None
    academy_cohort_stipend: Optional[int] = None

    # Platform
    status: str = "draft"
    show_in_directory: bool = False
    is_featured: bool = False
    average_rating: float = 0.0
    rating_count: int = 0

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# MEMBER RESPONSE SCHEMAS
# ============================================================================


class MemberResponse(BaseModel):
    """Full member response with nested sub-records."""

    # Core Identity
    id: uuid.UUID
    auth_id: str
    email: EmailStr
    first_name: str
    last_name: str

    # Status
    is_active: bool
    registration_complete: bool
    roles: Optional[list[str]] = None

    # Approval
    approval_status: str = "pending"
    approval_notes: Optional[str] = None
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None

    # Profile Photo (on core for quick access)
    profile_photo_url: Optional[str] = None

    # Timestamps
    created_at: datetime
    updated_at: datetime

    # Nested sub-records (optional, may be None if not created yet)
    profile: Optional[MemberProfileResponse] = None
    emergency_contact: Optional[MemberEmergencyContactResponse] = None
    availability: Optional[MemberAvailabilityResponse] = None
    membership: Optional[MemberMembershipResponse] = None
    preferences: Optional[MemberPreferencesResponse] = None
    coach_profile: Optional[CoachProfileResponse] = None

    model_config = ConfigDict(from_attributes=True)


class MemberListResponse(BaseModel):
    """Lightweight member response for lists (no nested sub-records)."""

    id: uuid.UUID
    auth_id: str
    email: EmailStr
    first_name: str
    last_name: str
    is_active: bool
    registration_complete: bool
    roles: Optional[list[str]] = None
    approval_status: str = "pending"
    profile_photo_url: Optional[str] = None
    created_at: datetime

    # Convenience field
    is_coach: bool = False

    model_config = ConfigDict(from_attributes=True)


class MemberPublicResponse(BaseModel):
    """Public member info (minimal)."""

    id: uuid.UUID
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# INPUT SCHEMAS
# ============================================================================


class MemberProfileInput(BaseModel):
    """Input for creating/updating profile."""

    phone: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    time_zone: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    occupation: Optional[str] = None
    area_in_lagos: Optional[str] = None
    swim_level: Optional[str] = None
    deep_water_comfort: Optional[str] = None
    strokes: Optional[list[str]] = None
    interests: Optional[list[str]] = None
    personal_goals: Optional[str] = None
    how_found_us: Optional[str] = None
    previous_communities: Optional[str] = None
    hopes_from_swimbuddz: Optional[str] = None
    social_instagram: Optional[str] = None
    social_linkedin: Optional[str] = None
    social_other: Optional[str] = None
    show_in_directory: Optional[bool] = None
    interest_tags: Optional[list[str]] = None


class MemberEmergencyContactInput(BaseModel):
    """Input for creating/updating emergency contact."""

    name: Optional[str] = None
    contact_relationship: Optional[str] = None
    phone: Optional[str] = None
    region: Optional[str] = None
    medical_info: Optional[str] = None
    safety_notes: Optional[str] = None


class MemberAvailabilityInput(BaseModel):
    """Input for creating/updating availability."""

    available_days: Optional[list[str]] = None
    preferred_times: Optional[list[str]] = None
    preferred_locations: Optional[list[str]] = None
    accessible_facilities: Optional[list[str]] = None
    travel_flexibility: Optional[str] = None
    equipment_needed: Optional[list[str]] = None


class MemberMembershipInput(BaseModel):
    """Input for creating/updating membership (admin only for most fields)."""

    primary_tier: Optional[str] = None
    active_tiers: Optional[list[str]] = None
    requested_tiers: Optional[list[str]] = None
    # Billing fields are typically set by payments_service
    club_notes: Optional[str] = None
    academy_goals: Optional[str] = None
    academy_preferred_coach_gender: Optional[str] = None
    academy_lesson_preference: Optional[str] = None
    academy_focus_areas: Optional[list[str]] = None


class MemberPreferencesInput(BaseModel):
    """Input for creating/updating preferences."""

    language_preference: Optional[str] = None
    comms_preference: Optional[str] = None
    payment_readiness: Optional[str] = None
    currency_preference: Optional[str] = None
    consent_photo: Optional[str] = None
    community_rules_accepted: Optional[bool] = None
    volunteer_interest: Optional[list[str]] = None
    volunteer_roles_detail: Optional[str] = None
    discovery_source: Optional[str] = None


class MemberCreate(BaseModel):
    """Input for creating a new member."""

    auth_id: str
    email: EmailStr
    first_name: str
    last_name: str

    # Optional nested inputs
    profile: Optional[MemberProfileInput] = None
    emergency_contact: Optional[MemberEmergencyContactInput] = None
    availability: Optional[MemberAvailabilityInput] = None
    membership: Optional[MemberMembershipInput] = None
    preferences: Optional[MemberPreferencesInput] = None


class MemberUpdate(BaseModel):
    """Input for updating a member."""

    # Core fields
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    profile_photo_url: Optional[str] = None

    # Nested updates
    profile: Optional[MemberProfileInput] = None
    emergency_contact: Optional[MemberEmergencyContactInput] = None
    availability: Optional[MemberAvailabilityInput] = None
    membership: Optional[MemberMembershipInput] = None
    preferences: Optional[MemberPreferencesInput] = None


# ============================================================================
# REGISTRATION SCHEMAS
# ============================================================================


class PendingRegistrationCreate(BaseModel):
    """Input for creating a pending registration."""

    email: EmailStr
    first_name: str
    last_name: str
    password: Optional[str] = None

    # Profile data (stored as JSON, parsed on completion)
    profile: Optional[MemberProfileInput] = None
    emergency_contact: Optional[MemberEmergencyContactInput] = None
    availability: Optional[MemberAvailabilityInput] = None
    preferences: Optional[MemberPreferencesInput] = None

    model_config = ConfigDict(extra="allow")


class PendingRegistrationResponse(BaseModel):
    """Response for pending registration."""

    id: uuid.UUID
    email: EmailStr
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# ADMIN SCHEMAS
# ============================================================================


class ApprovalAction(BaseModel):
    """Schema for approve/reject actions."""

    notes: Optional[str] = None


class ActivateCommunityRequest(BaseModel):
    """Request to activate community membership."""

    years: int = Field(default=1, ge=1, le=5)


class ActivateClubRequest(BaseModel):
    """Request to activate club membership."""

    months: int = Field(default=1, ge=1, le=12)
    skip_community_check: bool = Field(
        default=False,
        description="Skip community active check (for bundle activations where community was just activated)",
    )


class PendingMemberResponse(MemberResponse):
    """Extended response for pending members (admin view)."""

    pass
