"""Pydantic schemas for the swim readiness assessment."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AssessmentSubmit(BaseModel):
    """Request body for submitting an assessment."""

    answers: dict[str, int] = Field(
        ...,
        description="Map of question_id → selected option score",
        examples=[{"water_comfort": 2, "face_in_water": 3, "floating": 1}],
    )


class DimensionScoreResponse(BaseModel):
    """Single dimension score in the assessment result."""

    dimension: str
    label: str
    icon: str
    score: int
    max_score: int = Field(alias="maxScore")
    percentage: int
    rating: str  # strong | moderate | needs_work

    model_config = ConfigDict(populate_by_name=True)


class AssessmentResponse(BaseModel):
    """Response for a single assessment result."""

    id: uuid.UUID
    total_score: int
    raw_score: int
    level: str
    dimension_scores: list[dict]
    created_at: datetime
    member_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)


class AssessmentStatsResponse(BaseModel):
    """Aggregate statistics across all assessments."""

    total_count: int
    level_distribution: dict[str, int]
    average_score: float
