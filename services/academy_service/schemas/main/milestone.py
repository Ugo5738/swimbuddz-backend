"""Milestone and milestone-review-event schemas."""

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from services.academy_service.models import (
    MilestoneEventType,
    MilestoneType,
    ProgressStatus,
    RequiredEvidence,
)


class MilestoneBase(BaseModel):
    name: str
    criteria: Optional[str] = None
    video_media_id: Optional[UUID] = None
    # Organization & Type
    order_index: int = 0
    milestone_type: MilestoneType = MilestoneType.SKILL
    # Assessment
    required_evidence: RequiredEvidence = RequiredEvidence.NONE
    rubric_json: Optional[Dict[str, Any]] = None


class MilestoneCreate(MilestoneBase):
    program_id: UUID


class MilestoneUpdate(BaseModel):
    name: Optional[str] = None
    criteria: Optional[str] = None
    video_media_id: Optional[UUID] = None
    order_index: Optional[int] = None
    milestone_type: Optional[MilestoneType] = None
    required_evidence: Optional[RequiredEvidence] = None
    rubric_json: Optional[Dict[str, Any]] = None


class MilestoneResponse(MilestoneBase):
    id: UUID
    program_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MilestoneReviewEventResponse(BaseModel):
    """One entry in the milestone review audit trail."""

    id: UUID
    progress_id: UUID
    enrollment_id: UUID
    milestone_id: UUID
    event_type: MilestoneEventType
    actor_id: UUID
    actor_role: str
    previous_status: Optional[ProgressStatus] = None
    new_status: ProgressStatus
    student_notes_snapshot: Optional[str] = None
    coach_notes_snapshot: Optional[str] = None
    evidence_media_id_snapshot: Optional[UUID] = None
    score_snapshot: Optional[int] = None
    # Override fields — populated only for ``event_type=OVERRIDE`` rows.
    # ``override_of_event_id`` chains back to the prior decision so the
    # full back-and-forth can be reconstructed by walking the chain.
    override_of_event_id: Optional[UUID] = None
    override_reason: Optional[str] = None
    ai_metadata: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
