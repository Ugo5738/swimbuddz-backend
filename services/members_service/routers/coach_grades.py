"""Coach grades and progression routes."""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post
from libs.db.config import AsyncSessionLocal
from services.members_service.models import CoachGrade, CoachProfile, Member
from services.members_service.schemas import (
    AdminUpdateCoachGrades,
    CoachGradesResponse,
    CoachProgressionStats,
    EligibleCoachListItem,
    ProgramCategoryEnum,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

logger = get_logger(__name__)

router = APIRouter(prefix="/coaches", tags=["coaches"])
admin_router = APIRouter(prefix="/admin/coaches", tags=["admin-coaches"])

# Map category enum to model field names
CATEGORY_TO_FIELD = {
    ProgramCategoryEnum.LEARN_TO_SWIM: "learn_to_swim_grade",
    ProgramCategoryEnum.SPECIAL_POPULATIONS: "special_populations_grade",
    ProgramCategoryEnum.INSTITUTIONAL: "institutional_grade",
    ProgramCategoryEnum.COMPETITIVE_ELITE: "competitive_elite_grade",
    ProgramCategoryEnum.CERTIFICATIONS: "certifications_grade",
    ProgramCategoryEnum.SPECIALIZED_DISCIPLINES: "specialized_disciplines_grade",
    ProgramCategoryEnum.ADJACENT_SERVICES: "adjacent_services_grade",
}


@router.get("/me/grades", response_model=CoachGradesResponse)
async def get_my_grades(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the current coach's grades across all categories."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile

        return CoachGradesResponse(
            coach_profile_id=str(coach.id),
            member_id=str(member.id),
            display_name=coach.display_name,
            learn_to_swim_grade=coach.learn_to_swim_grade,
            special_populations_grade=coach.special_populations_grade,
            institutional_grade=coach.institutional_grade,
            competitive_elite_grade=coach.competitive_elite_grade,
            certifications_grade=coach.certifications_grade,
            specialized_disciplines_grade=coach.specialized_disciplines_grade,
            adjacent_services_grade=coach.adjacent_services_grade,
            total_coaching_hours=coach.total_coaching_hours,
            cohorts_completed=coach.cohorts_completed,
            average_feedback_rating=coach.average_feedback_rating,
            swimbuddz_level=coach.swimbuddz_level,
            last_active_date=coach.last_active_date,
            first_aid_cert_expiry=coach.first_aid_cert_expiry,
            cpr_expiry_date=coach.cpr_expiry_date,
            lifeguard_expiry_date=coach.lifeguard_expiry_date,
        )


@router.get("/me/progression", response_model=CoachProgressionStats)
async def get_my_progression(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the current coach's progression statistics."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile

        # Determine highest grade and grades held
        grade_order = [CoachGrade.GRADE_1, CoachGrade.GRADE_2, CoachGrade.GRADE_3]
        grades_held = []
        highest_grade = None
        highest_level = -1

        for category, field_name in CATEGORY_TO_FIELD.items():
            grade = getattr(coach, field_name)
            if grade:
                grades_held.append(category.value)
                level = grade_order.index(grade)
                if level > highest_level:
                    highest_level = level
                    highest_grade = grade

        # Check for expiring credentials (within 30 days)
        today = date.today()
        expiring_soon = []
        credentials_valid = True

        if coach.first_aid_cert_expiry:
            if coach.first_aid_cert_expiry < today:
                credentials_valid = False
                expiring_soon.append("first_aid_expired")
            elif coach.first_aid_cert_expiry <= today + timedelta(days=30):
                expiring_soon.append("first_aid")

        if coach.cpr_expiry_date:
            cpr_date = coach.cpr_expiry_date.date()
            if cpr_date < today:
                credentials_valid = False
                expiring_soon.append("cpr_expired")
            elif cpr_date <= today + timedelta(days=30):
                expiring_soon.append("cpr")

        if coach.lifeguard_expiry_date:
            lifeguard_date = coach.lifeguard_expiry_date.date()
            if lifeguard_date < today:
                credentials_valid = False
                expiring_soon.append("lifeguard_expired")
            elif lifeguard_date <= today + timedelta(days=30):
                expiring_soon.append("lifeguard")

        # TODO: Get active cohorts count from academy service
        active_cohorts = 0

        return CoachProgressionStats(
            coach_profile_id=str(coach.id),
            total_coaching_hours=coach.total_coaching_hours,
            cohorts_completed=coach.cohorts_completed,
            active_cohorts=active_cohorts,
            average_feedback_rating=coach.average_feedback_rating,
            swimbuddz_level=coach.swimbuddz_level,
            highest_grade=highest_grade,
            grades_held=grades_held,
            credentials_valid=credentials_valid,
            expiring_soon=expiring_soon,
        )


# === Admin Grade Management Endpoints ===


@admin_router.get("/{coach_profile_id}/grades", response_model=CoachGradesResponse)
async def get_coach_grades(
    coach_profile_id: str,
    _admin: dict = Depends(require_admin),
):
    """Get a coach's grades (admin only)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CoachProfile)
            .options(selectinload(CoachProfile.member))
            .where(CoachProfile.id == coach_profile_id)
        )
        coach = result.scalar_one_or_none()

        if not coach:
            raise HTTPException(status_code=404, detail="Coach profile not found")

        return CoachGradesResponse(
            coach_profile_id=str(coach.id),
            member_id=str(coach.member_id),
            display_name=coach.display_name,
            learn_to_swim_grade=coach.learn_to_swim_grade,
            special_populations_grade=coach.special_populations_grade,
            institutional_grade=coach.institutional_grade,
            competitive_elite_grade=coach.competitive_elite_grade,
            certifications_grade=coach.certifications_grade,
            specialized_disciplines_grade=coach.specialized_disciplines_grade,
            adjacent_services_grade=coach.adjacent_services_grade,
            total_coaching_hours=coach.total_coaching_hours,
            cohorts_completed=coach.cohorts_completed,
            average_feedback_rating=coach.average_feedback_rating,
            swimbuddz_level=coach.swimbuddz_level,
            last_active_date=coach.last_active_date,
            first_aid_cert_expiry=coach.first_aid_cert_expiry,
            cpr_expiry_date=coach.cpr_expiry_date,
            lifeguard_expiry_date=coach.lifeguard_expiry_date,
        )


@admin_router.put("/{coach_profile_id}/grades", response_model=CoachGradesResponse)
async def update_coach_grades(
    coach_profile_id: str,
    data: AdminUpdateCoachGrades,
    admin: AuthUser = Depends(require_admin),
):
    """Update a coach's grades (admin only)."""
    admin_email = admin.email or "admin"

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CoachProfile)
            .options(selectinload(CoachProfile.member))
            .where(CoachProfile.id == coach_profile_id)
        )
        coach = result.scalar_one_or_none()

        if not coach:
            raise HTTPException(status_code=404, detail="Coach profile not found")

        # Update grades that were provided
        update_data = data.model_dump(exclude_unset=True, exclude={"admin_notes"})
        for field, value in update_data.items():
            if hasattr(coach, field) and value is not None:
                setattr(coach, field, value)

        # Update admin notes if provided
        if data.admin_notes:
            existing_notes = coach.admin_notes or ""
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            new_note = (
                f"\n[{timestamp}] Grade update by {admin_email}: {data.admin_notes}"
            )
            coach.admin_notes = existing_notes + new_note

        await session.commit()
        await session.refresh(coach)

        logger.info(
            f"Coach grades updated for {coach_profile_id} by {admin_email}",
            extra={"extra_fields": {"grades": update_data}},
        )

        return CoachGradesResponse(
            coach_profile_id=str(coach.id),
            member_id=str(coach.member_id),
            display_name=coach.display_name,
            learn_to_swim_grade=coach.learn_to_swim_grade,
            special_populations_grade=coach.special_populations_grade,
            institutional_grade=coach.institutional_grade,
            competitive_elite_grade=coach.competitive_elite_grade,
            certifications_grade=coach.certifications_grade,
            specialized_disciplines_grade=coach.specialized_disciplines_grade,
            adjacent_services_grade=coach.adjacent_services_grade,
            total_coaching_hours=coach.total_coaching_hours,
            cohorts_completed=coach.cohorts_completed,
            average_feedback_rating=coach.average_feedback_rating,
            swimbuddz_level=coach.swimbuddz_level,
            last_active_date=coach.last_active_date,
            first_aid_cert_expiry=coach.first_aid_cert_expiry,
            cpr_expiry_date=coach.cpr_expiry_date,
            lifeguard_expiry_date=coach.lifeguard_expiry_date,
        )


@admin_router.post("/{coach_profile_id}/suggest-grades")
async def suggest_coach_grades(
    coach_profile_id: str,
    _admin: AuthUser = Depends(require_admin),
):
    """
    Get AI-suggested grades for a coach based on their profile data.

    Proxies to the AI service's coach grade scoring endpoint.
    Returns recommended grade, rationale, strengths, and areas for improvement.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CoachProfile)
            .options(selectinload(CoachProfile.member))
            .where(CoachProfile.id == coach_profile_id)
        )
        coach = result.scalar_one_or_none()

        if not coach:
            raise HTTPException(status_code=404, detail="Coach profile not found")

        # Determine current highest grade
        grade_order = {"grade_1": 1, "grade_2": 2, "grade_3": 3}
        current_grade = None
        highest_level = 0
        for _category, field_name in CATEGORY_TO_FIELD.items():
            grade = getattr(coach, field_name)
            if grade and grade_order.get(grade, 0) > highest_level:
                highest_level = grade_order[grade]
                current_grade = grade

        # Collect certifications from the coach profile
        certifications = coach.certifications or []

        # Build AI scoring payload
        ai_payload = {
            "coach_id": str(coach.id),
            "coaching_hours": coach.total_coaching_hours or 0,
            "cohorts_completed": coach.cohorts_completed or 0,
            "feedback_rating": float(coach.average_feedback_rating or 0),
            "certifications": certifications,
            "shadow_evaluations_passed": 0,  # TODO: track shadow evaluations
            "current_grade": current_grade,
        }

    settings = get_settings()
    try:
        resp = await internal_post(
            service_url=settings.AI_SERVICE_URL,
            path="/ai/score/coach-grade",
            calling_service="members",
            json=ai_payload,
            timeout=30.0,
        )
    except Exception as e:
        logger.error(f"AI service call failed for coach grade suggestion: {e}")
        raise HTTPException(
            status_code=502,
            detail="AI service is unavailable. Please try again later.",
        )

    if resp.status_code != 200:
        logger.error(f"AI grade suggestion failed: {resp.status_code} – {resp.text}")
        raise HTTPException(
            status_code=502,
            detail=f"AI scoring service returned {resp.status_code}",
        )

    return resp.json()


@admin_router.get(
    "/eligible/{category}/{required_grade}",
    response_model=list[EligibleCoachListItem],
)
async def list_eligible_coaches(
    category: ProgramCategoryEnum,
    required_grade: str,
    _admin: dict = Depends(require_admin),
):
    """
    List coaches eligible for a cohort based on category and required grade.

    A coach is eligible if their grade for the category meets or exceeds
    the required grade. Grade hierarchy: GRADE_1 < GRADE_2 < GRADE_3.
    """
    # Validate and convert required_grade
    try:
        required = CoachGrade(required_grade)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid grade: {required_grade}. Must be grade_1, grade_2, or grade_3",
        )

    grade_field = CATEGORY_TO_FIELD.get(category)
    if not grade_field:
        raise HTTPException(status_code=400, detail=f"Invalid category: {category}")

    # Determine eligible grades (required and above)
    grade_order = [CoachGrade.GRADE_1, CoachGrade.GRADE_2, CoachGrade.GRADE_3]
    required_level = grade_order.index(required)
    eligible_grades = grade_order[required_level:]

    async with AsyncSessionLocal() as session:
        # Get coaches with the appropriate grade
        result = await session.execute(
            select(CoachProfile)
            .join(Member)
            .options(selectinload(CoachProfile.member))
            .where(
                CoachProfile.status.in_(["approved", "active"]),
                getattr(CoachProfile, grade_field).in_(eligible_grades),
            )
            .order_by(Member.first_name, Member.last_name)
        )
        profiles = result.scalars().all()

        return [
            EligibleCoachListItem(
                coach_profile_id=str(p.id),
                member_id=str(p.member_id),
                display_name=p.display_name,
                email=p.member.email,
                grade=getattr(p, grade_field),
                coaching_years=p.coaching_years or 0,
                average_rating=p.average_rating or 0.0,
                cohorts_completed=p.cohorts_completed,
                # TODO: Check active cohorts against max_cohorts_at_once
                is_available=True,
            )
            for p in profiles
        ]
