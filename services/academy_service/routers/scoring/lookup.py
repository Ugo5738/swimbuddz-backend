"""Generic scoring previews and dimension-label lookup."""

from fastapi import APIRouter
from services.academy_service.routers._shared import (
    AICoachSuggestion,
    AICoachSuggestionResponse,
    AIDimensionSuggestion,
    AIScoringRequest,
    AIScoringResponse,
    AsyncSession,
    AuthUser,
    CoachGrade,
    Cohort,
    CohortComplexityScore,
    CohortComplexityScoreCreate,
    CohortComplexityScoreResponse,
    CohortComplexityScoreUpdate,
    ComplexityScoreCalculateRequest,
    ComplexityScoreCalculation,
    Depends,
    DimensionLabelsResponse,
    EligibleCoachResponse,
    HTTPException,
    List,
    ProgramCategory,
    _GRADE_COLUMN_MAP,
    calculate_complexity_score,
    get_async_db,
    get_current_user,
    get_dimension_labels,
    get_eligible_coaches,
    get_logger,
    get_member_by_auth_id,
    get_settings,
    internal_post,
    require_admin,
    select,
    selectinload,
    status,
    utc_now,
    uuid,
)



# ============================================================================

logger = get_logger(__name__)
router = APIRouter(tags=["academy"])

@router.post("/scoring/calculate", response_model=ComplexityScoreCalculation)
async def preview_complexity_score(
    body: ComplexityScoreCalculateRequest,
    _: AuthUser = Depends(require_admin),
):
    """
    Preview complexity score calculation without saving.
    Useful for testing scores before committing to a cohort.
    """
    for i, score in enumerate(body.dimension_scores):
        if score < 1 or score > 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Dimension {i + 1} score must be between 1 and 5",
            )

    try:
        result = calculate_complexity_score(body.category, body.dimension_scores)
        return ComplexityScoreCalculation(**result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("/scoring/dimensions/{category}", response_model=DimensionLabelsResponse)
async def get_scoring_dimensions(
    category: ProgramCategory,
    _: AuthUser = Depends(require_admin),
):
    """
    Get the dimension labels for a specific program category.
    Useful for building the scoring UI.
    """
    try:
        labels = get_dimension_labels(category)
        return DimensionLabelsResponse(category=category, labels=labels)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
