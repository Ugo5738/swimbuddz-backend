"""Student-progress schemas."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from services.academy_service.models import ProgressStatus


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
