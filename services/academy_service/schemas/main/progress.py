"""Student-progress schemas."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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


class OverrideProgressRequest(BaseModel):
    """Admin (or AI service) override of a prior coach decision.

    Distinct from ``StudentProgressUpdate`` because an override:

    * **Must** carry an ``override_reason`` — every override is recorded
      in the audit trail and the reason is required justification.
    * Does **not** touch ``reviewed_by_coach_id`` — the original coach
      stays attributed to the live row. See ACADEMY_ADMIN_CONTROLS_DESIGN §5.4.
    * Optionally carries ``ai_metadata`` for AI-driven overrides
      (model version, confidence score, etc.). Caller's role/identity is
      derived from the JWT, not this field — including ``ai_metadata``
      from a human admin token is allowed (it's just attribution data
      that travels with the event row) but ``actor_role`` is always set
      from the auth context.
    """

    enrollment_id: UUID
    milestone_id: UUID
    new_status: ProgressStatus
    override_reason: str = Field(min_length=1, max_length=2000)
    # Optional carry-over to the live row. ``None`` means "leave the
    # current value untouched"; passing an empty string explicitly
    # clears the field.
    coach_notes: Optional[str] = None
    score: Optional[int] = None
    ai_metadata: Optional[dict[str, Any]] = None


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
