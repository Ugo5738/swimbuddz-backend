"""Pydantic schemas for the AI Service API."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# ============================================================================
# AI REQUEST SCHEMAS
# ============================================================================


class AIRequestResponse(BaseModel):
    id: str
    request_type: str
    model_provider: str
    model_name: str
    input_data: dict
    output_data: Optional[dict] = None
    status: str
    error_message: Optional[str] = None
    latency_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    langfuse_trace_id: Optional[str] = None
    created_at: datetime


class AIRequestListResponse(BaseModel):
    items: list[AIRequestResponse]
    total: int
    page: int
    page_size: int


# ============================================================================
# SCORING SCHEMAS
# ============================================================================


class CohortComplexityScoringRequest(BaseModel):
    """Input for AI-assisted cohort complexity scoring."""

    program_category: str
    age_group: str = Field(..., description="e.g., 'children_6_10', 'adults'")
    skill_level: str = Field(..., description="e.g., 'beginner_1', 'intermediate'")
    special_needs: Optional[str] = None
    location_type: str = Field(
        default="indoor_pool", description="e.g., 'indoor_pool', 'open_water'"
    )
    duration_weeks: int = Field(default=12)
    class_size: int = Field(default=8)


class DimensionScore(BaseModel):
    dimension: str
    score: int = Field(ge=1, le=5)
    rationale: str
    confidence: float = Field(ge=0, le=1)


class CohortComplexityScoringResponse(BaseModel):
    """AI-suggested cohort complexity scores."""

    dimensions: list[DimensionScore]
    total_score: float
    required_coach_grade: str
    overall_rationale: str
    confidence: float
    ai_request_id: str
    model_used: str


class CoachGradeScoringRequest(BaseModel):
    """Input for AI-assisted coach grade assessment."""

    coach_id: str
    coaching_hours: float = 0
    cohorts_completed: int = 0
    feedback_rating: float = 0
    certifications: list[str] = []
    shadow_evaluations_passed: int = 0
    current_grade: Optional[str] = None


class CoachGradeScoringResponse(BaseModel):
    """AI-suggested coach grade progression."""

    recommended_grade: str
    rationale: str
    areas_for_improvement: list[str]
    strengths: list[str]
    confidence: float
    ai_request_id: str
    model_used: str


# ============================================================================
# COACH SUGGESTION SCHEMAS
# ============================================================================


class CoachSuggestionRequest(BaseModel):
    """Input for AI-assisted coach suggestion."""

    program_category: str
    cohort_name: str = ""
    program_name: str = ""
    total_score: int = 0
    required_coach_grade: str = "grade_1"
    dimension_summary: str = ""
    location: str = ""
    capacity: int = 8
    coaches: list[dict] = []


class CoachRanking(BaseModel):
    member_id: str
    name: str = "Unknown"
    match_score: float = Field(ge=0, le=1)
    rationale: str = ""


class CoachSuggestionResponse(BaseModel):
    """AI-suggested coach rankings for a cohort."""

    rankings: list[CoachRanking]
    ai_request_id: str
    model_used: str


# ============================================================================
# MODEL CONFIG SCHEMAS
# ============================================================================


class AIModelConfigResponse(BaseModel):
    id: str
    provider: str
    model_name: str
    is_enabled: bool
    is_default: bool
    max_tokens: int
    temperature: float
    input_cost_per_1k: Optional[float] = None
    output_cost_per_1k: Optional[float] = None
    created_at: datetime
    updated_at: datetime


class AIModelConfigCreate(BaseModel):
    provider: str
    model_name: str
    is_enabled: bool = True
    is_default: bool = False
    max_tokens: int = 4096
    temperature: float = 0.1
    input_cost_per_1k: Optional[float] = None
    output_cost_per_1k: Optional[float] = None


# ============================================================================
# PROMPT TEMPLATE SCHEMAS
# ============================================================================


class AIPromptTemplateResponse(BaseModel):
    id: str
    name: str
    version: int
    is_active: bool
    system_prompt: str
    user_prompt_template: str
    output_schema: Optional[dict] = None
    created_at: datetime


class AIPromptTemplateCreate(BaseModel):
    name: str
    system_prompt: str
    user_prompt_template: str
    output_schema: Optional[dict] = None
