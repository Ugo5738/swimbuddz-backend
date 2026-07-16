"""AI Service schemas package."""

from services.ai_service.schemas.analysis import (
    AnalysisJobCreateRequest,
    AnalysisJobDetailResponse,
    AnalysisJobResponse,
    AnalysisResultPayload,
)
from services.ai_service.schemas.content import (
    ContentDraftPayload,
    ContentDraftRequest,
    ContentDraftResponse,
    ContentDraftSection,
    ContentImageRequest,
    ContentImageResponse,
)
from services.ai_service.schemas.main import (
    AIModelConfigCreate,
    AIModelConfigResponse,
    AIPromptTemplateCreate,
    AIPromptTemplateResponse,
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
    "AIRequestResponse",
    "AnalysisJobCreateRequest",
    "AnalysisJobDetailResponse",
    "AnalysisJobResponse",
    "AnalysisResultPayload",
    "ContentDraftPayload",
    "ContentDraftRequest",
    "ContentDraftResponse",
    "ContentDraftSection",
    "ContentImageRequest",
    "ContentImageResponse",
    "CoachGradeScoringRequest",
    "CoachGradeScoringResponse",
    "CoachRanking",
    "CoachSuggestionRequest",
    "CoachSuggestionResponse",
    "CohortComplexityScoringRequest",
    "CohortComplexityScoringResponse",
    "DimensionScore",
]
