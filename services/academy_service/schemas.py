from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from services.academy_service.models import (
    BillingType,
    CohortStatus,
    EnrollmentStatus,
    PaymentStatus,
    ProgramLevel,
    ProgressStatus,
)

# --- Program Schemas ---


class ProgramBase(BaseModel):
    name: str
    description: Optional[str] = None
    cover_image_url: Optional[str] = None
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
    cover_image_url: Optional[str] = None
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
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Milestone Schemas ---


class MilestoneBase(BaseModel):
    name: str
    criteria: Optional[str] = None
    video_url: Optional[str] = None


class MilestoneCreate(MilestoneBase):
    program_id: UUID


class MilestoneUpdate(BaseModel):
    name: Optional[str] = None
    criteria: Optional[str] = None
    video_url: Optional[str] = None


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
    content_url: Optional[str] = None
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
    status: ProgressStatus
    achieved_at: Optional[datetime] = None
    coach_notes: Optional[str] = None


class StudentProgressResponse(StudentProgressBase):
    id: UUID
    enrollment_id: UUID
    milestone_id: UUID
    achieved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Resolve forward reference
StudentProgressResponse.model_rebuild()
