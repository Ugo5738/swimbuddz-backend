from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from services.academy_service.models import (
    ProgramLevel, CohortStatus, EnrollmentStatus, PaymentStatus, ProgressStatus
)


# --- Program Schemas ---

class ProgramBase(BaseModel):
    name: str
    description: Optional[str] = None
    level: ProgramLevel
    duration_weeks: int
    curriculum_json: Optional[Dict[str, Any]] = None


class ProgramCreate(ProgramBase):
    pass


class ProgramUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    level: Optional[ProgramLevel] = None
    duration_weeks: Optional[int] = None
    curriculum_json: Optional[Dict[str, Any]] = None


class ProgramResponse(ProgramBase):
    id: UUID
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


class CohortCreate(CohortBase):
    program_id: UUID


class CohortUpdate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    capacity: Optional[int] = None
    status: Optional[CohortStatus] = None


class CohortResponse(CohortBase):
    id: UUID
    program_id: UUID
    created_at: datetime
    updated_at: datetime
    
    # Optional nested fields if needed, but keeping flat for now
    
    model_config = ConfigDict(from_attributes=True)


# --- Enrollment Schemas ---

class EnrollmentBase(BaseModel):
    status: EnrollmentStatus = EnrollmentStatus.ENROLLED
    payment_status: PaymentStatus = PaymentStatus.PENDING


class EnrollmentCreate(BaseModel):
    cohort_id: UUID
    member_id: UUID


class EnrollmentUpdate(BaseModel):
    status: Optional[EnrollmentStatus] = None
    payment_status: Optional[PaymentStatus] = None


class EnrollmentResponse(EnrollmentBase):
    id: UUID
    cohort_id: UUID
    member_id: UUID
    created_at: datetime
    updated_at: datetime

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
