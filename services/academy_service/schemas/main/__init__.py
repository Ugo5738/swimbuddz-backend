"""Re-export shim for academy schemas.

Original 965-line module was split into per-domain submodules (see
docs/CONVENTIONS.md §12). All existing
``from services.academy_service.schemas.main import X`` imports keep working
because everything is re-exported here. Pydantic forward references are
resolved at the bottom once every referenced class is in scope.
"""

from .ai_scoring import (
    AICoachSuggestion,
    AICoachSuggestionResponse,
    AIDimensionSuggestion,
    AIScoringRequest,
    AIScoringResponse,
)
from .coach_dashboard import (
    CoachCohortDetail,
    CoachDashboardSummary,
    MilestoneReviewAction,
    PendingMilestoneReview,
    UpcomingSessionSummary,
)
from .cohort import (
    CoachAssignmentInput,
    CohortBase,
    CohortCreate,
    CohortResourceBase,
    CohortResourceCreate,
    CohortResourceResponse,
    CohortResponse,
    CohortTimelineSessionImpact,
    CohortTimelineShiftApplyResponse,
    CohortTimelineShiftLogResponse,
    CohortTimelineShiftPreviewResponse,
    CohortTimelineShiftRequest,
    CohortUpdate,
)
from .cohort_extras import (
    CohortExtensionRequestCreate,
    CohortExtensionRequestResponse,
    CohortExtensionRequestReview,
    CohortWithScoreResponse,
)
from .complexity import (
    CohortComplexityScoreCreate,
    CohortComplexityScoreResponse,
    CohortComplexityScoreUpdate,
    ComplexityScoreCalculateRequest,
    ComplexityScoreCalculation,
    DimensionLabelsResponse,
    DimensionScore,
    EligibleCoachResponse,
)
from .enrollment import (
    AdminDropoutActionRequest,
    EnrollmentBase,
    EnrollmentCreate,
    EnrollmentInstallmentResponse,
    EnrollmentMarkPaidRequest,
    EnrollmentResponse,
    EnrollmentUpdate,
    WithdrawEnrollmentRequest,
    WithdrawEnrollmentResponse,
)
from .milestone import (
    MilestoneBase,
    MilestoneCreate,
    MilestoneResponse,
    MilestoneReviewEventResponse,
    MilestoneUpdate,
)
from .onboarding import NextSessionInfo, OnboardingResponse
from .program import ProgramBase, ProgramCreate, ProgramResponse, ProgramUpdate
from .progress import (
    MemberMilestoneClaimRequest,
    OverrideProgressRequest,
    StudentProgressBase,
    StudentProgressResponse,
    StudentProgressUpdate,
)

# Resolve forward references — must happen after every referenced class is
# in scope. `EnrollmentResponse` references `StudentProgressResponse` by name;
# `CoachDashboardSummary` references `UpcomingSessionSummary` by name.
StudentProgressResponse.model_rebuild()
EnrollmentResponse.model_rebuild()
CoachDashboardSummary.model_rebuild()

__all__ = [
    # ai_scoring
    "AICoachSuggestion",
    "AICoachSuggestionResponse",
    "AIDimensionSuggestion",
    "AIScoringRequest",
    "AIScoringResponse",
    # coach_dashboard
    "CoachCohortDetail",
    "CoachDashboardSummary",
    "MilestoneReviewAction",
    "PendingMilestoneReview",
    "UpcomingSessionSummary",
    # cohort
    "CoachAssignmentInput",
    "CohortBase",
    "CohortCreate",
    "CohortResourceBase",
    "CohortResourceCreate",
    "CohortResourceResponse",
    "CohortResponse",
    "CohortTimelineSessionImpact",
    "CohortTimelineShiftApplyResponse",
    "CohortTimelineShiftLogResponse",
    "CohortTimelineShiftPreviewResponse",
    "CohortTimelineShiftRequest",
    "CohortUpdate",
    # cohort_extras
    "CohortExtensionRequestCreate",
    "CohortExtensionRequestResponse",
    "CohortExtensionRequestReview",
    "CohortWithScoreResponse",
    # complexity
    "CohortComplexityScoreCreate",
    "CohortComplexityScoreResponse",
    "CohortComplexityScoreUpdate",
    "ComplexityScoreCalculateRequest",
    "ComplexityScoreCalculation",
    "DimensionLabelsResponse",
    "DimensionScore",
    "EligibleCoachResponse",
    # enrollment
    "AdminDropoutActionRequest",
    "EnrollmentBase",
    "EnrollmentCreate",
    "EnrollmentInstallmentResponse",
    "EnrollmentMarkPaidRequest",
    "EnrollmentResponse",
    "EnrollmentUpdate",
    "WithdrawEnrollmentRequest",
    "WithdrawEnrollmentResponse",
    # milestone
    "MilestoneBase",
    "MilestoneCreate",
    "MilestoneResponse",
    "MilestoneReviewEventResponse",
    "MilestoneUpdate",
    # onboarding
    "NextSessionInfo",
    "OnboardingResponse",
    # program
    "ProgramBase",
    "ProgramCreate",
    "ProgramResponse",
    "ProgramUpdate",
    # progress
    "MemberMilestoneClaimRequest",
    "OverrideProgressRequest",
    "StudentProgressBase",
    "StudentProgressResponse",
    "StudentProgressUpdate",
]
