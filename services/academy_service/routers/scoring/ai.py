"""AI-driven complexity scoring + coach suggestion."""

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
    Depends,
    HTTPException,
    ProgramCategory,
    _GRADE_COLUMN_MAP,
    calculate_complexity_score,
    get_async_db,
    get_dimension_labels,
    get_eligible_coaches,
    get_logger,
    get_settings,
    internal_post,
    require_admin,
    select,
    selectinload,
    status,
    uuid,
)


# ============================================================================

logger = get_logger(__name__)
router = APIRouter(tags=["academy"])


@router.post(
    "/cohorts/{cohort_id}/ai-score",
    response_model=AIScoringResponse,
)
async def ai_score_cohort(
    cohort_id: uuid.UUID,
    body: AIScoringRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get AI-suggested dimension scores for a cohort.

    The AI analyses the cohort/program metadata and returns suggested
    scores across the 7 category-specific dimensions.  The admin can
    then review, adjust and save manually.
    """
    # Load cohort + program
    result = await db.execute(
        select(Cohort)
        .options(selectinload(Cohort.program))
        .where(Cohort.id == cohort_id)
    )
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    program = cohort.program

    # Determine the category (explicit request > existing score > fallback)
    category = body.category
    if category is None:
        # Try to use the existing complexity score category
        score_result = await db.execute(
            select(CohortComplexityScore).where(
                CohortComplexityScore.cohort_id == cohort_id
            )
        )
        existing = score_result.scalar_one_or_none()
        if existing:
            category = existing.category
    if category is None:
        category = ProgramCategory.LEARN_TO_SWIM  # safe default

    # Build the AI request payload
    ai_payload = {
        "program_category": (
            category.value if hasattr(category, "value") else str(category)
        ),
        "age_group": body.age_group or "mixed",
        "skill_level": body.skill_level
        or (program.level if program and hasattr(program, "level") else "beginner_1"),
        "special_needs": body.special_needs,
        "location_type": body.location_type
        or (
            cohort.location_type.value
            if cohort.location_type and hasattr(cohort.location_type, "value")
            else "indoor_pool"
        ),
        "duration_weeks": body.duration_weeks
        or (program.duration_weeks if program else 12),
        "class_size": body.class_size or cohort.capacity or 8,
    }

    settings = get_settings()
    resp = await internal_post(
        service_url=settings.AI_SERVICE_URL,
        path="/ai/score/cohort-complexity",
        calling_service="academy",
        json=ai_payload,
        timeout=30.0,  # AI calls may take longer
    )

    if resp.status_code != 200:
        detail = resp.text
        logger.error(f"AI scoring failed: {resp.status_code} – {detail}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI scoring service returned {resp.status_code}",
        )

    ai_result = resp.json()

    # Get the category-specific dimension labels
    dim_labels = get_dimension_labels(category)

    # Map AI generic dimension names to category-specific labels
    ai_dimensions = ai_result.get("dimensions", [])
    suggestions: list[AIDimensionSuggestion] = []
    for i in range(7):
        dim = ai_dimensions[i] if i < len(ai_dimensions) else {}
        suggestions.append(
            AIDimensionSuggestion(
                dimension=dim.get("dimension", f"dimension_{i + 1}"),
                label=dim_labels[i] if i < len(dim_labels) else f"Dimension {i + 1}",
                score=max(1, min(5, dim.get("score", 3))),
                rationale=dim.get("rationale", ""),
                confidence=dim.get("confidence", 0.8),
            )
        )

    # Calculate totals from the AI-suggested scores
    scores = [s.score for s in suggestions]
    calc = calculate_complexity_score(category, scores)

    return AIScoringResponse(
        dimensions=suggestions,
        total_score=calc["total_score"],
        required_coach_grade=calc["required_coach_grade"],
        pay_band_min=calc["pay_band_min"],
        pay_band_max=calc["pay_band_max"],
        overall_rationale=ai_result.get("overall_rationale", ""),
        confidence=ai_result.get("confidence", 0.8),
        model_used=ai_result.get("model_used", "unknown"),
        ai_request_id=ai_result.get("ai_request_id"),
    )


@router.post(
    "/cohorts/{cohort_id}/ai-suggest-coach",
    response_model=AICoachSuggestionResponse,
)
async def ai_suggest_coach(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get AI-ranked coach suggestions for a scored cohort.

    The endpoint fetches eligible coaches, then uses AI to rank them
    by suitability based on the cohort's complexity profile and the
    coach's experience/specialisation.
    """
    # Load cohort with complexity score
    result = await db.execute(
        select(Cohort)
        .options(
            selectinload(Cohort.complexity_score),
            selectinload(Cohort.program),
        )
        .where(Cohort.id == cohort_id)
    )
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    if not cohort.required_coach_grade or not cohort.complexity_score:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cohort must be scored before requesting coach suggestions.",
        )

    score = cohort.complexity_score
    category = (
        ProgramCategory(score.category)
        if isinstance(score.category, str)
        else score.category
    )
    required_grade = (
        CoachGrade(cohort.required_coach_grade)
        if isinstance(cohort.required_coach_grade, str)
        else cohort.required_coach_grade
    )

    # Get eligible coaches
    grade_column = _GRADE_COLUMN_MAP.get(category, "learn_to_swim_grade")
    grade_order = [CoachGrade.GRADE_1, CoachGrade.GRADE_2, CoachGrade.GRADE_3]
    required_level = grade_order.index(required_grade)
    eligible_grades = [g.value for g in grade_order[required_level:]]

    coach_rows = await get_eligible_coaches(
        grade_column, eligible_grades, calling_service="academy"
    )

    if not coach_rows:
        return AICoachSuggestionResponse(
            suggestions=[],
            required_coach_grade=required_grade,
            category=category,
            model_used="none",
        )

    # Get dimension labels for context
    dim_labels = get_dimension_labels(category)

    # Build dimension summary string
    dim_summary = "; ".join(
        f"{dim_labels[i]}: {getattr(score, f'dimension_{i + 1}_score', '?')}/5"
        for i in range(7)
    )

    # Build the AI prompt payload
    coaches_info = [
        {
            "member_id": str(c["member_id"]),
            "name": c.get("name", "Unknown"),
            "grade": c.get("grade", "grade_1"),
            "total_coaching_hours": c.get("total_coaching_hours", 0),
            "average_feedback_rating": c.get("average_feedback_rating"),
        }
        for c in coach_rows
    ]

    ai_payload = {
        "program_category": category.value,
        "cohort_name": cohort.name,
        "program_name": cohort.program.name if cohort.program else "",
        "total_score": score.total_score,
        "required_coach_grade": required_grade.value,
        "dimension_summary": dim_summary,
        "location": cohort.location_name or "",
        "capacity": cohort.capacity,
        "coaches": coaches_info,
    }

    settings = get_settings()
    resp = await internal_post(
        service_url=settings.AI_SERVICE_URL,
        path="/ai/score/suggest-coach",
        calling_service="academy",
        json=ai_payload,
        timeout=30.0,
    )

    if resp.status_code != 200:
        logger.error(f"AI coach suggestion failed: {resp.status_code} – {resp.text}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI service returned {resp.status_code}",
        )

    ai_result = resp.json()

    # Build response — merge AI rankings with coach data
    coach_map = {str(c["member_id"]): c for c in coach_rows}
    suggestions: list[AICoachSuggestion] = []

    for ranked in ai_result.get("rankings", []):
        mid = ranked.get("member_id", "")
        coach = coach_map.get(mid, {})
        suggestions.append(
            AICoachSuggestion(
                member_id=mid,
                name=coach.get("name", ranked.get("name", "Unknown")),
                email=coach.get("email"),
                grade=CoachGrade(coach.get("grade", "grade_1")),
                total_coaching_hours=coach.get("total_coaching_hours", 0),
                average_feedback_rating=coach.get("average_feedback_rating"),
                match_score=ranked.get("match_score", 0.5),
                rationale=ranked.get("rationale", ""),
            )
        )

    return AICoachSuggestionResponse(
        suggestions=suggestions,
        required_coach_grade=required_grade,
        category=category,
        model_used=ai_result.get("model_used", "unknown"),
        ai_request_id=ai_result.get("ai_request_id"),
    )
