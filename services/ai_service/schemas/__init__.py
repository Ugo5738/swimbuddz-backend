"""AI Service schemas package."""

from services.ai_service.schemas.main import (
    AIModelConfigCreate,
    AIModelConfigResponse,
    AIPromptTemplateCreate,
    AIPromptTemplateResponse,
    AIRequestListResponse,
    AIRequestResponse,
    CoachGradeScoringRequest,
    CoachGradeScoringResponse,
    CoachRanking,
    CoachSuggestionRequest,
    CoachSuggestionResponse,
    CohortComplexityScoringRequest,
    CohortComplexityScoringResponse,
    DimensionScore,
)

__all__ = [
    "AIModelConfigCreate",
    "AIModelConfigResponse",
    "AIPromptTemplateCreate",
    "AIPromptTemplateResponse",
    "AIRequestListResponse",
    "AIRequestResponse",
    "CoachGradeScoringRequest",
    "CoachGradeScoringResponse",
    "CoachRanking",
    "CoachSuggestionRequest",
    "CoachSuggestionResponse",
    "CohortComplexityScoringRequest",
    "CohortComplexityScoringResponse",
    "DimensionScore",
]
