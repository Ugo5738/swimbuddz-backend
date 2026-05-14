"""Cohort schema extras: with-score response + extension-request flow."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from services.academy_service.models import CoachGrade

from .cohort import CohortResponse
from .complexity import CohortComplexityScoreResponse


class CohortWithScoreResponse(CohortResponse):
    """Cohort response including complexity score if available."""

    required_coach_grade: Optional[CoachGrade] = None
    complexity_score: Optional[CohortComplexityScoreResponse] = None


class CohortExtensionRequestCreate(BaseModel):
    """Coach request to extend a cohort's end date."""

    weeks_requested: int = Field(
        ge=1, le=4, description="Number of weeks to extend (1-4)"
    )
    reason: str = Field(
        min_length=10, max_length=1000, description="Reason for the extension"
    )


class CohortExtensionRequestResponse(BaseModel):
    """Response for a cohort extension request."""

    id: UUID
    cohort_id: UUID
    coach_id: UUID
    weeks_requested: int
    reason: str
    current_end_date: datetime
    proposed_end_date: datetime
    status: str
    reviewed_by_id: Optional[UUID] = None
    admin_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CohortExtensionRequestReview(BaseModel):
    """Admin action on an extension request."""

    admin_notes: Optional[str] = Field(None, max_length=500)
