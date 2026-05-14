"""AI-assisted cohort scoring and coach-recommendation schemas."""

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from services.academy_service.models import CoachGrade, ProgramCategory


class AIScoringRequest(BaseModel):
    """Request body for AI-assisted cohort complexity scoring.

    All fields are optional — if omitted, the backend will try to derive
    them from the cohort / program data.
    """

    category: Optional[ProgramCategory] = None
    age_group: Optional[str] = None
    skill_level: Optional[str] = None
    special_needs: Optional[str] = None
    location_type: Optional[str] = None
    duration_weeks: Optional[int] = None
    class_size: Optional[int] = None


class AIDimensionSuggestion(BaseModel):
    """A single AI-suggested dimension score."""

    dimension: str
    label: str
    score: int = Field(ge=1, le=5)
    rationale: str
    confidence: float = Field(ge=0, le=1)


class AIScoringResponse(BaseModel):
    """AI-suggested complexity scores for a cohort."""

    dimensions: List[AIDimensionSuggestion]
    total_score: int
    required_coach_grade: CoachGrade
    pay_band_min: int
    pay_band_max: int
    overall_rationale: str
    confidence: float
    model_used: str
    ai_request_id: Optional[str] = None


class AICoachSuggestion(BaseModel):
    """A single AI-recommended coach for a cohort."""

    member_id: UUID
    name: str
    email: Optional[str] = None
    grade: CoachGrade
    total_coaching_hours: Optional[int] = None
    average_feedback_rating: Optional[float] = None
    match_score: float = Field(ge=0, le=1, description="0-1 suitability score")
    rationale: str


class AICoachSuggestionResponse(BaseModel):
    """AI-suggested coaches ranked by suitability."""

    suggestions: List[AICoachSuggestion]
    required_coach_grade: CoachGrade
    category: ProgramCategory
    model_used: str
    ai_request_id: Optional[str] = None
