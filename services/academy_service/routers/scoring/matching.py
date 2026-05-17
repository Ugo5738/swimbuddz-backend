"""Eligible-coaches lookup for a cohort."""

from fastapi import APIRouter
from services.academy_service.routers._shared import (
    AsyncSession,
    AuthUser,
    CoachGrade,
    Cohort,
    Depends,
    EligibleCoachResponse,
    HTTPException,
    List,
    ProgramCategory,
    get_async_db,
    get_eligible_coaches,
    get_logger,
    require_admin,
    select,
    selectinload,
    status,
    uuid,
)


# ============================================================================

logger = get_logger(__name__)
router = APIRouter(tags=["academy"])


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
