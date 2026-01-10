"""Coach-specific schemas for application, onboarding, and admin review."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

# === Coach Application ===


class CoachApplicationCreate(BaseModel):
    """Schema for submitting a coach application."""

    # Account info (if creating new account)
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    # Application fields
    display_name: Optional[str] = None
    short_bio: str = Field(..., min_length=20, max_length=500)
    full_bio: Optional[str] = None

    coaching_years: int = Field(..., ge=0, le=50)
    coaching_experience_summary: Optional[str] = None
    coaching_specialties: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    other_certifications_note: Optional[str] = None
    coaching_document_link: Optional[str] = None
    coaching_document_file_name: Optional[str] = None

    # Optional professional info
    levels_taught: Optional[list[str]] = None
    age_groups_taught: Optional[list[str]] = None
    languages_spoken: Optional[list[str]] = None
    coaching_portfolio_link: Optional[str] = None

    # Safety/compliance
    has_cpr_training: bool = False
    cpr_expiry_date: Optional[datetime] = None


class CoachApplicationResponse(BaseModel):
    """Response for coach application status."""

    id: str
    member_id: str
    email: str
    first_name: str
    last_name: str
    display_name: Optional[str] = None

    status: str  # draft, pending_review, more_info_needed, approved, rejected, active
    short_bio: Optional[str] = None
    coaching_years: int = 0
    coaching_specialties: list[str] = []
    certifications: list[str] = []
    coaching_document_link: Optional[str] = None
    coaching_document_file_name: Optional[str] = None
    other_certifications_note: Optional[str] = None

    levels_taught: Optional[list[str]] = None
    age_groups_taught: Optional[list[str]] = None
    languages_spoken: Optional[list[str]] = None
    coaching_portfolio_link: Optional[str] = None

    has_cpr_training: bool = False
    cpr_expiry_date: Optional[datetime] = None

    application_submitted_at: Optional[datetime] = None
    application_reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class CoachApplicationStatusResponse(BaseModel):
    """Minimal status response for coaches checking their application."""

    status: str
    application_submitted_at: Optional[datetime] = None
    application_reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    can_access_dashboard: bool = False


# === Coach Onboarding ===


class CoachOnboardingUpdate(BaseModel):
    """Schema for coach onboarding data."""

    # Availability
    availability_calendar: Optional[dict] = None  # JSON structure for availability

    # Locations
    pools_supported: Optional[list[str]] = None
    can_travel_between_pools: bool = False
    travel_radius_km: Optional[float] = None

    # Session preferences
    accepts_one_on_one: bool = True
    accepts_group_cohorts: bool = True
    max_swimmers_per_session: int = 10
    max_cohorts_at_once: int = 1
    preferred_cohort_types: Optional[list[str]] = None

    # Pricing & visibility
    currency: Optional[str] = None
    one_to_one_rate_per_hour: Optional[int] = None
    group_session_rate_per_hour: Optional[int] = None
    academy_cohort_stipend: Optional[int] = None
    show_in_directory: Optional[bool] = None

    # Profile
    coach_profile_photo_media_id: Optional[uuid.UUID] = None


# === Coach Profile Update ===


class CoachProfileUpdate(BaseModel):
    """Schema for updating coach profile."""

    display_name: Optional[str] = None
    short_bio: Optional[str] = None
    full_bio: Optional[str] = None
    coach_profile_photo_media_id: Optional[uuid.UUID] = None

    coaching_years: Optional[int] = None
    coaching_experience_summary: Optional[str] = None
    coaching_specialties: Optional[list[str]] = None
    certifications: Optional[list[str]] = None
    other_certifications_note: Optional[str] = None

    levels_taught: Optional[list[str]] = None
    age_groups_taught: Optional[list[str]] = None
    languages_spoken: Optional[list[str]] = None
    coaching_portfolio_link: Optional[str] = None

    has_cpr_training: Optional[bool] = None
    cpr_expiry_date: Optional[datetime] = None

    availability_calendar: Optional[dict] = None
    pools_supported: Optional[list[str]] = None
    can_travel_between_pools: Optional[bool] = None
    travel_radius_km: Optional[float] = None

    accepts_one_on_one: Optional[bool] = None
    accepts_group_cohorts: Optional[bool] = None
    max_swimmers_per_session: Optional[int] = None
    max_cohorts_at_once: Optional[int] = None
    preferred_cohort_types: Optional[list[str]] = None


# === Admin Coach Review ===


class AdminCoachApplicationListItem(BaseModel):
    """Condensed coach application for admin list view."""

    id: str
    member_id: str
    email: str
    first_name: str
    last_name: str
    display_name: Optional[str] = None
    status: str
    coaching_years: int = 0
    coaching_specialties: list[str] = []
    certifications: list[str] = []
    coaching_document_link: Optional[str] = None
    coaching_document_file_name: Optional[str] = None
    application_submitted_at: Optional[datetime] = None
    created_at: datetime


class AdminCoachApplicationDetail(BaseModel):
    """Full coach application for admin review."""

    id: str
    member_id: str
    email: str
    first_name: str
    last_name: str
    phone: Optional[str] = None

    # Identity
    display_name: Optional[str] = None
    coach_profile_photo_url: Optional[str] = None
    short_bio: Optional[str] = None
    full_bio: Optional[str] = None

    # Professional
    certifications: list[str] = []
    other_certifications_note: Optional[str] = None
    coaching_years: int = 0
    coaching_experience_summary: Optional[str] = None
    coaching_document_link: Optional[str] = None
    coaching_document_file_name: Optional[str] = None
    coaching_specialties: list[str] = []
    levels_taught: list[str] = []
    age_groups_taught: list[str] = []
    languages_spoken: list[str] = []
    coaching_portfolio_link: Optional[str] = None

    # Safety
    has_cpr_training: bool = False
    cpr_expiry_date: Optional[datetime] = None
    background_check_status: str = "not_required"
    background_check_document_url: Optional[str] = None

    # Status
    status: str
    application_submitted_at: Optional[datetime] = None
    application_reviewed_at: Optional[datetime] = None
    application_reviewed_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    admin_notes: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class AdminApproveCoach(BaseModel):
    """Payload for approving a coach application."""

    admin_notes: Optional[str] = None


class AdminRejectCoach(BaseModel):
    """Payload for rejecting a coach application."""

    rejection_reason: str = Field(..., min_length=10)
    admin_notes: Optional[str] = None


class AdminRequestMoreInfo(BaseModel):
    """Payload for requesting more info from coach applicant."""

    message: str = Field(..., min_length=10)
    admin_notes: Optional[str] = None
