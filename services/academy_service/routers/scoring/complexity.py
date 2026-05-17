"""Per-cohort complexity score CRUD + review."""

from fastapi import APIRouter
from services.academy_service.routers._shared import (
    AsyncSession,
    AuthUser,
    Cohort,
    CohortComplexityScore,
    CohortComplexityScoreCreate,
    CohortComplexityScoreResponse,
    CohortComplexityScoreUpdate,
    Depends,
    HTTPException,
    calculate_complexity_score,
    get_async_db,
    get_current_user,
    get_logger,
    get_member_by_auth_id,
    require_admin,
    select,
    status,
    utc_now,
    uuid,
)


# ============================================================================

logger = get_logger(__name__)
router = APIRouter(tags=["academy"])


@router.post(
    "/cohorts/{cohort_id}/complexity-score",
    response_model=CohortComplexityScoreResponse,
)
async def create_cohort_complexity_score(
    cohort_id: uuid.UUID,
    score_data: CohortComplexityScoreCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a complexity score for a cohort.
    This determines the required coach grade and pay band.
    """
    # Check cohort exists
    result = await db.execute(select(Cohort).where(Cohort.id == cohort_id))
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    # Check if score already exists
    result = await db.execute(
        select(CohortComplexityScore).where(
            CohortComplexityScore.cohort_id == cohort_id
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Complexity score already exists for this cohort. Use PUT to update.",
        )

    # Get member_id from auth user via members service
    member_info = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member_info:
        raise HTTPException(status_code=404, detail="Member not found")
    member_id = member_info["id"]

    # Extract dimension scores
    dimension_scores = [
        score_data.dimension_1.score,
        score_data.dimension_2.score,
        score_data.dimension_3.score,
        score_data.dimension_4.score,
        score_data.dimension_5.score,
        score_data.dimension_6.score,
        score_data.dimension_7.score,
    ]

    # Calculate score
    try:
        calc_result = calculate_complexity_score(score_data.category, dimension_scores)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Create the score record
    complexity_score = CohortComplexityScore(
        cohort_id=cohort_id,
        category=score_data.category,
        dimension_1_score=score_data.dimension_1.score,
        dimension_1_rationale=score_data.dimension_1.rationale,
        dimension_2_score=score_data.dimension_2.score,
        dimension_2_rationale=score_data.dimension_2.rationale,
        dimension_3_score=score_data.dimension_3.score,
        dimension_3_rationale=score_data.dimension_3.rationale,
        dimension_4_score=score_data.dimension_4.score,
        dimension_4_rationale=score_data.dimension_4.rationale,
        dimension_5_score=score_data.dimension_5.score,
        dimension_5_rationale=score_data.dimension_5.rationale,
        dimension_6_score=score_data.dimension_6.score,
        dimension_6_rationale=score_data.dimension_6.rationale,
        dimension_7_score=score_data.dimension_7.score,
        dimension_7_rationale=score_data.dimension_7.rationale,
        total_score=calc_result["total_score"],
        required_coach_grade=calc_result["required_coach_grade"],
        pay_band_min=calc_result["pay_band_min"],
        pay_band_max=calc_result["pay_band_max"],
        scored_by_id=member_id,
        scored_at=utc_now(),
    )

    db.add(complexity_score)

    # Update cohort with required grade
    cohort.required_coach_grade = calc_result["required_coach_grade"]

    await db.commit()
    await db.refresh(complexity_score)

    return complexity_score


@router.get(
    "/cohorts/{cohort_id}/complexity-score",
    response_model=CohortComplexityScoreResponse,
)
async def get_cohort_complexity_score(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get the complexity score for a cohort.
    """
    result = await db.execute(
        select(CohortComplexityScore).where(
            CohortComplexityScore.cohort_id == cohort_id
        )
    )
    score = result.scalar_one_or_none()
    if not score:
        raise HTTPException(status_code=404, detail="Complexity score not found")

    return score


@router.put(
    "/cohorts/{cohort_id}/complexity-score",
    response_model=CohortComplexityScoreResponse,
)
async def update_cohort_complexity_score(
    cohort_id: uuid.UUID,
    score_data: CohortComplexityScoreUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update the complexity score for a cohort.
    """
    result = await db.execute(
        select(CohortComplexityScore).where(
            CohortComplexityScore.cohort_id == cohort_id
        )
    )
    score = result.scalar_one_or_none()
    if not score:
        raise HTTPException(status_code=404, detail="Complexity score not found")

    # Get member_id from auth user for reviewer tracking
    member_info = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    member_id = member_info["id"] if member_info else None

    # Update category if provided
    if score_data.category is not None:
        score.category = score_data.category

    # Update dimensions if provided
    if score_data.dimension_1 is not None:
        score.dimension_1_score = score_data.dimension_1.score
        score.dimension_1_rationale = score_data.dimension_1.rationale
    if score_data.dimension_2 is not None:
        score.dimension_2_score = score_data.dimension_2.score
        score.dimension_2_rationale = score_data.dimension_2.rationale
    if score_data.dimension_3 is not None:
        score.dimension_3_score = score_data.dimension_3.score
        score.dimension_3_rationale = score_data.dimension_3.rationale
    if score_data.dimension_4 is not None:
        score.dimension_4_score = score_data.dimension_4.score
        score.dimension_4_rationale = score_data.dimension_4.rationale
    if score_data.dimension_5 is not None:
        score.dimension_5_score = score_data.dimension_5.score
        score.dimension_5_rationale = score_data.dimension_5.rationale
    if score_data.dimension_6 is not None:
        score.dimension_6_score = score_data.dimension_6.score
        score.dimension_6_rationale = score_data.dimension_6.rationale
    if score_data.dimension_7 is not None:
        score.dimension_7_score = score_data.dimension_7.score
        score.dimension_7_rationale = score_data.dimension_7.rationale

    # Recalculate totals
    dimension_scores = [
        score.dimension_1_score,
        score.dimension_2_score,
        score.dimension_3_score,
        score.dimension_4_score,
        score.dimension_5_score,
        score.dimension_6_score,
        score.dimension_7_score,
    ]

    try:
        calc_result = calculate_complexity_score(score.category, dimension_scores)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    score.total_score = calc_result["total_score"]
    score.required_coach_grade = calc_result["required_coach_grade"]
    score.pay_band_min = calc_result["pay_band_min"]
    score.pay_band_max = calc_result["pay_band_max"]
    score.reviewed_by_id = member_id
    score.reviewed_at = utc_now()

    # Update cohort with required grade
    result = await db.execute(select(Cohort).where(Cohort.id == cohort_id))
    cohort = result.scalar_one_or_none()
    if cohort:
        cohort.required_coach_grade = calc_result["required_coach_grade"]

    await db.commit()
    await db.refresh(score)

    return score


@router.delete("/cohorts/{cohort_id}/complexity-score")
async def delete_cohort_complexity_score(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete the complexity score for a cohort.
    """
    result = await db.execute(
        select(CohortComplexityScore).where(
            CohortComplexityScore.cohort_id == cohort_id
        )
    )
    score = result.scalar_one_or_none()
    if not score:
        raise HTTPException(status_code=404, detail="Complexity score not found")

    await db.delete(score)

    # Clear required grade from cohort
    result = await db.execute(select(Cohort).where(Cohort.id == cohort_id))
    cohort = result.scalar_one_or_none()
    if cohort:
        cohort.required_coach_grade = None

    await db.commit()

    return {"message": "Complexity score deleted"}


@router.post("/cohorts/{cohort_id}/complexity-score/review")
async def mark_complexity_score_reviewed(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Mark a complexity score as reviewed (for audit purposes).
    """
    result = await db.execute(
        select(CohortComplexityScore).where(
            CohortComplexityScore.cohort_id == cohort_id
        )
    )
    score = result.scalar_one_or_none()
    if not score:
        raise HTTPException(status_code=404, detail="Complexity score not found")

    # Get member_id from auth user via members service
    member_info = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    member_id = member_info["id"] if member_info else None

    score.reviewed_by_id = member_id
    score.reviewed_at = utc_now()

    await db.commit()

    return {"message": "Complexity score marked as reviewed"}
