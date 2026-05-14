"""Cohort complexity-scoring schemas (manual scoring, not AI)."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.academy_service.models import CoachGrade, ProgramCategory


class DimensionScore(BaseModel):
    """Individual dimension score with optional rationale."""

    score: int
    rationale: Optional[str] = None

    @field_validator("score")
    @classmethod
    def validate_score(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError("Score must be between 1 and 5")
        return v


class CohortComplexityScoreCreate(BaseModel):
    """Create a complexity score for a cohort."""

    category: ProgramCategory
    dimension_1: DimensionScore
    dimension_2: DimensionScore
    dimension_3: DimensionScore
    dimension_4: DimensionScore
    dimension_5: DimensionScore
    dimension_6: DimensionScore
    dimension_7: DimensionScore


class CohortComplexityScoreUpdate(BaseModel):
    """Update a complexity score for a cohort."""

    category: Optional[ProgramCategory] = None
    dimension_1: Optional[DimensionScore] = None
    dimension_2: Optional[DimensionScore] = None
    dimension_3: Optional[DimensionScore] = None
    dimension_4: Optional[DimensionScore] = None
    dimension_5: Optional[DimensionScore] = None
    dimension_6: Optional[DimensionScore] = None
    dimension_7: Optional[DimensionScore] = None


class CohortComplexityScoreResponse(BaseModel):
    """Response schema for cohort complexity score."""

    id: UUID
    cohort_id: UUID
    category: ProgramCategory

    # Dimension scores
    dimension_1_score: int
    dimension_1_rationale: Optional[str] = None
    dimension_2_score: int
    dimension_2_rationale: Optional[str] = None
    dimension_3_score: int
    dimension_3_rationale: Optional[str] = None
    dimension_4_score: int
    dimension_4_rationale: Optional[str] = None
    dimension_5_score: int
    dimension_5_rationale: Optional[str] = None
    dimension_6_score: int
    dimension_6_rationale: Optional[str] = None
    dimension_7_score: int
    dimension_7_rationale: Optional[str] = None

    # Calculated fields
    total_score: int
    required_coach_grade: CoachGrade
    pay_band_min: int  # Percentage as integer (e.g., 45 = 45%)
    pay_band_max: int

    # Audit
    scored_by_id: UUID
    scored_at: datetime
    reviewed_by_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ComplexityScoreCalculateRequest(BaseModel):
    """Request body for previewing a complexity score calculation."""

    category: ProgramCategory
    dimension_scores: List[int] = Field(..., min_length=7, max_length=7)


class ComplexityScoreCalculation(BaseModel):
    """Preview of complexity score calculation without saving."""

    total_score: int
    required_coach_grade: CoachGrade
    pay_band_min: int
    pay_band_max: int


class DimensionLabelsResponse(BaseModel):
    """Dimension labels for a given program category (UI contract)."""

    category: ProgramCategory
    labels: List[str]


class EligibleCoachResponse(BaseModel):
    """Coach eligible for a cohort based on grade requirements."""

    member_id: UUID
    name: str
    email: Optional[str] = None
    grade: CoachGrade
    total_coaching_hours: Optional[int] = None
    average_feedback_rating: Optional[float] = None
