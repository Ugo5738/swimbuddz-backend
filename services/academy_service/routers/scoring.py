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

router = APIRouter(tags=["academy"])
logger = get_logger(__name__)


# ============================================================================
# COHORT COMPLEXITY SCORING
# ============================================================================


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


@router.get(
    "/cohorts/{cohort_id}/eligible-coaches", response_model=List[EligibleCoachResponse]
)
async def get_eligible_coaches_for_cohort(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get coaches eligible for a cohort based on grade requirements.
    Returns coaches who meet or exceed the required grade.
    """
    # Get cohort with complexity score
    result = await db.execute(
        select(Cohort)
        .options(selectinload(Cohort.complexity_score))
        .where(Cohort.id == cohort_id)
    )
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    if not cohort.required_coach_grade:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cohort has no complexity score. Score the cohort first.",
        )

    required_grade = cohort.required_coach_grade

    # Query coaches with grades that meet or exceed requirement
    # Note: This queries the coach_profiles table which stores grades
    # The grade columns are: learn_to_swim_grade, special_populations_grade, etc.
    # For now, we'll query based on a general coach grade
    # In production, this should match the category-specific grade

    # Get the category from complexity score
    category = None
    if cohort.complexity_score:
        category = cohort.complexity_score.category

    # Map category to grade column name
    grade_column_map = {
        ProgramCategory.LEARN_TO_SWIM: "learn_to_swim_grade",
        ProgramCategory.SPECIAL_POPULATIONS: "special_populations_grade",
        ProgramCategory.INSTITUTIONAL: "institutional_grade",
        ProgramCategory.COMPETITIVE_ELITE: "competitive_elite_grade",
        ProgramCategory.CERTIFICATIONS: "certifications_grade",
        ProgramCategory.SPECIALIZED_DISCIPLINES: "specialized_disciplines_grade",
        ProgramCategory.ADJACENT_SERVICES: "adjacent_services_grade",
    }

    grade_column = grade_column_map.get(category, "learn_to_swim_grade")

    # Build the eligible grades list
    grade_order = [CoachGrade.GRADE_1, CoachGrade.GRADE_2, CoachGrade.GRADE_3]
    required_level = grade_order.index(required_grade)
    eligible_grades = [g.value for g in grade_order[required_level:]]

    # Query eligible coaches via members-service
    rows = await get_eligible_coaches(
        grade_column, eligible_grades, calling_service="academy"
    )

    return [
        EligibleCoachResponse(
            member_id=row["member_id"],
            name=row["name"] or "Unknown",
            email=row["email"],
            grade=CoachGrade(row["grade"]) if row.get("grade") else CoachGrade.GRADE_1,
            total_coaching_hours=row.get("total_coaching_hours", 0),
            average_feedback_rating=row.get("average_feedback_rating"),
        )
        for row in rows
    ]


# ============================================================================
# AI-ASSISTED SCORING
# ============================================================================


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


# ============================================================================
# AI-ASSISTED COACH SUGGESTION
# ============================================================================


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
