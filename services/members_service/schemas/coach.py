"""Coach-specific schemas for application, onboarding, and admin review."""

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

# ============================================================================
# ENUMS (mirror the model enums for API serialization)
# ============================================================================


class CoachGradeEnum(str, Enum):
    """Coach grade levels for API responses."""

    GRADE_1 = "grade_1"
    GRADE_2 = "grade_2"
    GRADE_3 = "grade_3"


class ProgramCategoryEnum(str, Enum):
    """Program categories for grade assignment."""

    LEARN_TO_SWIM = "learn_to_swim"
    SPECIAL_POPULATIONS = "special_populations"
    INSTITUTIONAL = "institutional"
    COMPETITIVE_ELITE = "competitive_elite"
    CERTIFICATIONS = "certifications"
    SPECIALIZED_DISCIPLINES = "specialized_disciplines"
    ADJACENT_SERVICES = "adjacent_services"


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
    show_in_directory: Optional[bool] = True

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


class CoachPreferencesUpdate(BaseModel):
    """Schema for updating coach preferences after onboarding."""

    availability_calendar: Optional[dict] = None
    pools_supported: Optional[list[str]] = None
    can_travel_between_pools: Optional[bool] = None
    travel_radius_km: Optional[float] = None
    accepts_one_on_one: Optional[bool] = None
    accepts_group_cohorts: Optional[bool] = None
    max_swimmers_per_session: Optional[int] = None
    max_cohorts_at_once: Optional[int] = None
    preferred_cohort_types: Optional[list[str]] = None
    show_in_directory: Optional[bool] = None


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


# === Coach Bank Account ===


class BankAccountCreate(BaseModel):
    """Schema for creating/updating coach bank account."""

    bank_code: str = Field(..., min_length=3, max_length=10)
    bank_name: str = Field(..., min_length=2, max_length=100)
    account_number: str = Field(..., min_length=10, max_length=20)
    # account_name is typically auto-verified via Paystack Resolve API


class BankAccountResponse(BaseModel):
    """Response for coach bank account."""

    id: str
    member_id: str
    bank_code: str
    bank_name: str
    account_number: str
    account_name: str
    is_verified: bool
    verified_at: Optional[datetime] = None
    paystack_recipient_code: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class BankListResponse(BaseModel):
    """Nigerian bank for dropdown."""

    name: str
    code: str
    slug: str


class ResolveAccountRequest(BaseModel):
    """Request to resolve/verify a bank account."""

    bank_code: str = Field(..., min_length=3, max_length=10)
    account_number: str = Field(..., min_length=10, max_length=20)


class ResolveAccountResponse(BaseModel):
    """Response from bank account resolution."""

    account_number: str
    account_name: str
    bank_code: str


# ============================================================================
# COACH GRADES & PROGRESSION
# ============================================================================


class CoachGradesResponse(BaseModel):
    """Coach grades across all program categories."""

    coach_profile_id: str
    member_id: str
    display_name: Optional[str] = None

    # Grades by category
    learn_to_swim_grade: Optional[CoachGradeEnum] = None
    special_populations_grade: Optional[CoachGradeEnum] = None
    institutional_grade: Optional[CoachGradeEnum] = None
    competitive_elite_grade: Optional[CoachGradeEnum] = None
    certifications_grade: Optional[CoachGradeEnum] = None
    specialized_disciplines_grade: Optional[CoachGradeEnum] = None
    adjacent_services_grade: Optional[CoachGradeEnum] = None

    # Progression stats
    total_coaching_hours: int = 0
    cohorts_completed: int = 0
    average_feedback_rating: Optional[float] = None
    swimbuddz_level: Optional[int] = None
    last_active_date: Optional[date] = None

    # Credentials
    first_aid_cert_expiry: Optional[date] = None
    cpr_expiry_date: Optional[datetime] = None
    lifeguard_expiry_date: Optional[datetime] = None


class AdminUpdateCoachGrades(BaseModel):
    """Admin payload for updating coach grades."""

    # Individual category grades (all optional - only update provided fields)
    learn_to_swim_grade: Optional[CoachGradeEnum] = None
    special_populations_grade: Optional[CoachGradeEnum] = None
    institutional_grade: Optional[CoachGradeEnum] = None
    competitive_elite_grade: Optional[CoachGradeEnum] = None
    certifications_grade: Optional[CoachGradeEnum] = None
    specialized_disciplines_grade: Optional[CoachGradeEnum] = None
    adjacent_services_grade: Optional[CoachGradeEnum] = None

    # Optional: update SwimBuddz internal level
    swimbuddz_level: Optional[int] = Field(None, ge=1, le=3)

    # Admin notes for audit trail
    admin_notes: Optional[str] = None


class CoachCategoryGradeUpdate(BaseModel):
    """Update a single category grade for a coach."""

    category: ProgramCategoryEnum
    grade: CoachGradeEnum
    admin_notes: Optional[str] = None


class CoachProgressionStats(BaseModel):
    """Coach progression statistics for dashboard."""

    coach_profile_id: str
    total_coaching_hours: int = 0
    cohorts_completed: int = 0
    active_cohorts: int = 0
    average_feedback_rating: Optional[float] = None
    swimbuddz_level: Optional[int] = None

    # Grade summary
    highest_grade: Optional[CoachGradeEnum] = None
    grades_held: list[str] = []  # List of categories where coach has grades

    # Credentials status
    credentials_valid: bool = True
    expiring_soon: list[str] = []  # List of credentials expiring within 30 days


class CoachEligibilityCheck(BaseModel):
    """Response for checking coach eligibility for a cohort."""

    coach_profile_id: str
    display_name: Optional[str] = None
    is_eligible: bool
    required_grade: CoachGradeEnum
    coach_grade: Optional[CoachGradeEnum] = None
    category: ProgramCategoryEnum
    reason: Optional[str] = None  # Explanation if not eligible


class EligibleCoachListItem(BaseModel):
    """Coach item in eligible coaches list."""

    coach_profile_id: str
    member_id: str
    display_name: Optional[str] = None
    email: str
    grade: CoachGradeEnum
    coaching_years: int = 0
    average_rating: float = 0.0
    cohorts_completed: int = 0
    is_available: bool = True  # Based on max_cohorts_at_once


# ============================================================================
# COACH AGREEMENT SCHEMAS
# ============================================================================


class SignatureTypeEnum(str, Enum):
    """Signature type for coach agreements."""

    TYPED_NAME = "typed_name"
    DRAWN = "drawn"
    CHECKBOX = "checkbox"
    UPLOADED_IMAGE = "uploaded_image"


class AgreementContentResponse(BaseModel):
    """Current agreement content for display."""

    version: str
    title: str
    content: str  # Markdown or HTML content
    content_hash: str  # SHA-256 hash for verification
    effective_date: date
    requires_signature: bool = True


class SignAgreementRequest(BaseModel):
    """Request to sign the coach agreement."""

    signature_type: SignatureTypeEnum
    signature_data: str = Field(
        ...,
        description=(
            "Typed name string, base64 encoded drawing, "
            "'CHECKBOX_AGREE' for checkbox, or media reference for uploaded image"
        ),
    )
    signature_media_id: Optional[str] = Field(
        None,
        description="Media service ID for uploaded signature images (required when signature_type is uploaded_image)",
    )
    agreement_version: str
    agreement_content_hash: str  # Must match current version
    handbook_acknowledged: bool = Field(
        ...,
        description="Must be True — coach must acknowledge the handbook before signing",
    )
    handbook_version: Optional[str] = Field(
        None,
        description="Version of the handbook that was acknowledged",
    )


class CoachAgreementResponse(BaseModel):
    """Response for a signed coach agreement."""

    id: str
    coach_profile_id: str
    agreement_version: str
    signature_type: str
    signed_at: datetime
    is_active: bool

    # Audit info (partial)
    ip_address: Optional[str] = None  # Only show last octet for privacy

    created_at: datetime


class CoachAgreementStatusResponse(BaseModel):
    """Quick status check for coach agreement."""

    has_signed_current_version: bool
    current_version: str
    signed_version: Optional[str] = None
    signed_at: Optional[datetime] = None
    requires_new_signature: bool = False


class CoachAgreementHistoryItem(BaseModel):
    """Historical agreement for audit trail."""

    id: str
    agreement_version: str
    signature_type: str
    signed_at: datetime
    is_active: bool
    superseded_at: Optional[datetime] = None


# ============================================================================
# AGREEMENT VERSION MANAGEMENT (Admin)
# ============================================================================


class AgreementVersionListItem(BaseModel):
    """Agreement version for admin list view."""

    id: str
    version: str
    title: str
    effective_date: date
    is_current: bool
    content_hash: str
    signature_count: int = 0
    created_at: datetime


class AgreementVersionDetail(BaseModel):
    """Full agreement version detail for admin view."""

    id: str
    version: str
    title: str
    content: str
    content_hash: str
    effective_date: date
    is_current: bool
    created_by_id: Optional[str] = None
    signature_count: int = 0
    active_signature_count: int = 0
    created_at: datetime
    updated_at: datetime


class CreateAgreementVersionRequest(BaseModel):
    """Admin request to create a new agreement version."""

    version: str = Field(..., min_length=1, max_length=20)
    title: str = Field(..., min_length=5, max_length=200)
    content: str = Field(..., min_length=100)
    effective_date: date


# ============================================================================
# HANDBOOK VERSION MANAGEMENT
# ============================================================================


class HandbookContentResponse(BaseModel):
    """Current handbook content for display."""

    version: str
    title: str
    content: str  # Markdown content
    content_hash: str
    effective_date: date


class HandbookVersionListItem(BaseModel):
    """Handbook version for admin list view."""

    id: str
    version: str
    title: str
    effective_date: date
    is_current: bool
    content_hash: str
    created_at: datetime


class HandbookVersionDetail(BaseModel):
    """Full handbook version detail for admin view."""

    id: str
    version: str
    title: str
    content: str
    content_hash: str
    effective_date: date
    is_current: bool
    created_by_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CreateHandbookVersionRequest(BaseModel):
    """Admin request to create a new handbook version."""

    version: str = Field(..., min_length=1, max_length=20)
    title: str = Field(..., min_length=5, max_length=200)
    content: str = Field(..., min_length=100)
    effective_date: date
