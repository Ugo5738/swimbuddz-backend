from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from services.academy_service.models import (
    BillingType,
    CohortStatus,
    EnrollmentStatus,
    LocationType,
    PaymentStatus,
    ProgramLevel,
    ProgressStatus,
)

# --- Program Schemas ---


class ProgramBase(BaseModel):
    name: str
    description: Optional[str] = None
    cover_image_media_id: Optional[UUID] = None
    level: ProgramLevel
    duration_weeks: int
    default_capacity: int = 10
    # Pricing
    currency: str = "NGN"
    price_amount: int = 0  # In naira (major unit)
    billing_type: BillingType = BillingType.ONE_TIME
    # Content
    curriculum_json: Optional[Dict[str, Any]] = None
    prep_materials: Optional[Dict[str, Any]] = None
    # Status
    is_published: bool = False


class ProgramCreate(ProgramBase):
    pass


class ProgramUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cover_image_media_id: Optional[UUID] = None
    level: Optional[ProgramLevel] = None
    duration_weeks: Optional[int] = None
    default_capacity: Optional[int] = None
    currency: Optional[str] = None
    price_amount: Optional[int] = None
    billing_type: Optional[BillingType] = None
    curriculum_json: Optional[Dict[str, Any]] = None
    prep_materials: Optional[Dict[str, Any]] = None
    is_published: Optional[bool] = None


class ProgramResponse(ProgramBase):
    id: UUID
    version: int = 1
    cover_image_url: Optional[str] = None  # Resolved from media_id
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Milestone Schemas ---


class MilestoneBase(BaseModel):
    name: str
    criteria: Optional[str] = None
    video_media_id: Optional[UUID] = None


class MilestoneCreate(MilestoneBase):
    program_id: UUID


class MilestoneUpdate(BaseModel):
    name: Optional[str] = None
    criteria: Optional[str] = None
    video_media_id: Optional[UUID] = None


class MilestoneResponse(MilestoneBase):
    id: UUID
    program_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Cohort Schemas ---


class CohortBase(BaseModel):
    name: str
    start_date: datetime
    end_date: datetime
    capacity: int
    status: CohortStatus = CohortStatus.OPEN
    allow_mid_entry: bool = False
    require_approval: bool = (
        False  # If True, enrollment needs admin approval even after payment
    )
    # Location
    timezone: Optional[str] = None
    location_type: Optional[LocationType] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    # Pricing override
    price_override: Optional[int] = None
    notes_internal: Optional[str] = None


class CohortCreate(CohortBase):
    program_id: UUID
    coach_id: Optional[UUID] = None


class CohortUpdate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    capacity: Optional[int] = None
    status: Optional[CohortStatus] = None
    coach_id: Optional[UUID] = None
    allow_mid_entry: Optional[bool] = None
    require_approval: Optional[bool] = None
    # Location
    timezone: Optional[str] = None
    location_type: Optional[LocationType] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    # Pricing override
    price_override: Optional[int] = None
    notes_internal: Optional[str] = None


class CohortResponse(CohortBase):
    id: UUID
    program_id: UUID
    coach_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    # Include program details for UI convenience (avoid extra client round trips).
    program: Optional[ProgramResponse] = None

    model_config = ConfigDict(from_attributes=True)


# --- Cohort Resource Schemas ---


class CohortResourceBase(BaseModel):
    title: str
    resource_type: str  # 'note', 'drill', 'assignment'
    content_media_id: Optional[UUID] = None
    description: Optional[str] = None


class CohortResourceCreate(CohortResourceBase):
    cohort_id: UUID


class CohortResourceResponse(CohortResourceBase):
    id: UUID
    cohort_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Enrollment Schemas ---


class EnrollmentBase(BaseModel):
    status: EnrollmentStatus = EnrollmentStatus.ENROLLED
    payment_status: PaymentStatus = PaymentStatus.PENDING


class EnrollmentCreate(BaseModel):
    program_id: UUID
    cohort_id: Optional[UUID] = None
    member_id: UUID
    preferences: Optional[Dict[str, Any]] = None


class EnrollmentUpdate(BaseModel):
    status: Optional[EnrollmentStatus] = None
    payment_status: Optional[PaymentStatus] = None
    cohort_id: Optional[UUID] = None  # Allow assigning/changing cohort


class EnrollmentResponse(EnrollmentBase):
    id: UUID
    program_id: Optional[UUID] = (
        None  # Optional for backward compat if needed, but model has it nullable=True
    )
    cohort_id: Optional[UUID] = None
    member_id: UUID
    preferences: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    # Include details for UI
    cohort: Optional[CohortResponse] = None
    program: Optional[ProgramResponse] = None

    model_config = ConfigDict(from_attributes=True)


# --- Student Progress Schemas ---


class StudentProgressBase(BaseModel):
    status: ProgressStatus = ProgressStatus.PENDING
    coach_notes: Optional[str] = None


class StudentProgressUpdate(BaseModel):
    """Admin/Coach update - can set status, achievement time, and notes."""

    status: ProgressStatus
    achieved_at: Optional[datetime] = None
    coach_notes: Optional[str] = None
    reviewed_by_coach_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None


class MemberMilestoneClaimRequest(BaseModel):
    """Member self-claim for a milestone - includes optional evidence via media service."""

    evidence_media_id: Optional[UUID] = (
        None  # Links to uploaded file or external URL in media service
    )
    student_notes: Optional[str] = None


class StudentProgressResponse(StudentProgressBase):
    id: UUID
    enrollment_id: UUID
    milestone_id: UUID
    achieved_at: Optional[datetime] = None
    evidence_media_id: Optional[UUID] = None
    student_notes: Optional[str] = None
    score: Optional[int] = None
    reviewed_by_coach_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Resolve forward reference
StudentProgressResponse.model_rebuild()


# --- Onboarding Schema ---


class NextSessionInfo(BaseModel):
    """Information about the next scheduled session."""

    date: Optional[datetime] = None
    location: Optional[str] = None
    notes: Optional[str] = None


class OnboardingResponse(BaseModel):
    """Structured onboarding information for a new enrollment."""

    enrollment_id: UUID
    program_name: str
    cohort_name: str
    start_date: datetime
    end_date: datetime
    location: Optional[str] = None
    next_session: Optional[NextSessionInfo] = None
    prep_materials: Optional[Dict[str, Any]] = None
    dashboard_link: str
    resources_link: str
    sessions_link: str
    coach_name: Optional[str] = None
    total_milestones: int
