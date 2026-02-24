"""Coach assignment and shadow evaluation API routes."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user, require_admin, require_coach
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    get_coach_readiness_data,
    get_member_by_auth_id,
    get_member_by_id,
)
from libs.db.session import get_async_db
from services.academy_service.models import CoachAssignment, Cohort, ShadowEvaluation
from services.academy_service.schemas import (
    AssignmentRoleEnum,
    CoachAssignmentCreate,
    CoachAssignmentResponse,
    CoachAssignmentUpdate,
    CoachReadinessResponse,
    ReadinessCheckItem,
    ReadinessCheckStatus,
    ShadowEvaluationCreate,
    ShadowEvaluationResponse,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = get_logger(__name__)

router = APIRouter(prefix="/coach-assignments", tags=["coach-assignments"])


# ── Helpers ──


async def _get_member_name(member_id: uuid.UUID) -> str:
    """Get a member's display name by ID via members-service."""
    member = await get_member_by_id(str(member_id), calling_service="academy")
    if not member:
        return "Unknown"
    return f"{member['first_name']} {member['last_name']}"


def _assignment_to_response(
    a: CoachAssignment,
    coach_name: str = None,
    cohort_name: str = None,
    program_name: str = None,
) -> CoachAssignmentResponse:
    return CoachAssignmentResponse(
        id=str(a.id),
        cohort_id=str(a.cohort_id),
        coach_id=str(a.coach_id),
        role=a.role,
        start_date=a.start_date,
        end_date=a.end_date,
        assigned_by_id=str(a.assigned_by_id),
        status=a.status,
        notes=a.notes,
        is_session_override=a.is_session_override,
        session_date=a.session_date,
        created_at=a.created_at,
        updated_at=a.updated_at,
        coach_name=coach_name,
        cohort_name=cohort_name,
        program_name=program_name,
    )


async def _get_member_id_from_auth(auth_id: str) -> Optional[uuid.UUID]:
    """Get member ID from Supabase auth_id via members-service."""
    member = await get_member_by_auth_id(auth_id, calling_service="academy")
    if not member:
        return None
    return uuid.UUID(member["id"])


# ── Assignment Endpoints ──


@router.post("/", response_model=CoachAssignmentResponse)
async def create_assignment(
    data: CoachAssignmentCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new coach assignment (admin only)."""
    # Verify cohort exists
    cohort_result = await db.execute(
        select(Cohort)
        .options(selectinload(Cohort.program))
        .where(Cohort.id == data.cohort_id)
    )
    cohort = cohort_result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    # Get admin member ID
    admin_id = await _get_member_id_from_auth(current_user.user_id)
    if not admin_id:
        raise HTTPException(status_code=400, detail="Admin member not found")

    assignment = CoachAssignment(
        cohort_id=data.cohort_id,
        coach_id=data.coach_id,
        role=data.role.value,
        start_date=data.start_date or utc_now(),
        end_date=data.end_date,
        assigned_by_id=admin_id,
        status="active",
        notes=data.notes,
        is_session_override=data.is_session_override,
        session_date=data.session_date,
    )
    db.add(assignment)

    # Also set Cohort.coach_id for backward compat when assigning a lead
    if data.role == AssignmentRoleEnum.LEAD and not data.is_session_override:
        cohort.coach_id = data.coach_id

    await db.commit()
    await db.refresh(assignment)

    coach_name = await _get_member_name(data.coach_id)
    return _assignment_to_response(
        assignment,
        coach_name=coach_name,
        cohort_name=cohort.name,
        program_name=cohort.program.name if cohort.program else None,
    )


@router.get("/cohort/{cohort_id}", response_model=list[CoachAssignmentResponse])
async def list_cohort_assignments(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List all coach assignments for a cohort."""
    result = await db.execute(
        select(CoachAssignment)
        .where(CoachAssignment.cohort_id == cohort_id)
        .order_by(CoachAssignment.created_at.desc())
    )
    assignments = result.scalars().all()

    responses = []
    for a in assignments:
        coach_name = await _get_member_name(a.coach_id)
        responses.append(_assignment_to_response(a, coach_name=coach_name))
    return responses


@router.get("/coach/me", response_model=list[CoachAssignmentResponse])
async def list_my_assignments(
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """List my coach assignments."""
    member_id = await _get_member_id_from_auth(current_user.user_id)
    if not member_id:
        raise HTTPException(status_code=404, detail="Member not found")

    result = await db.execute(
        select(CoachAssignment)
        .options(selectinload(CoachAssignment.cohort))
        .where(
            CoachAssignment.coach_id == member_id,
            CoachAssignment.status == "active",
        )
        .order_by(CoachAssignment.created_at.desc())
    )
    assignments = result.scalars().all()

    responses = []
    for a in assignments:
        cohort = a.cohort
        responses.append(
            _assignment_to_response(
                a,
                cohort_name=cohort.name if cohort else None,
            )
        )
    return responses


@router.get("/coach/{coach_id}", response_model=list[CoachAssignmentResponse])
async def list_coach_assignments(
    coach_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all assignments for a specific coach (admin only)."""
    result = await db.execute(
        select(CoachAssignment)
        .options(selectinload(CoachAssignment.cohort))
        .where(CoachAssignment.coach_id == coach_id)
        .order_by(CoachAssignment.created_at.desc())
    )
    assignments = result.scalars().all()

    coach_name = await _get_member_name(coach_id)
    responses = []
    for a in assignments:
        cohort = a.cohort
        responses.append(
            _assignment_to_response(
                a,
                coach_name=coach_name,
                cohort_name=cohort.name if cohort else None,
            )
        )
    return responses


@router.patch("/{assignment_id}", response_model=CoachAssignmentResponse)
async def update_assignment(
    assignment_id: uuid.UUID,
    data: CoachAssignmentUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a coach assignment (admin only)."""
    result = await db.execute(
        select(CoachAssignment).where(CoachAssignment.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    update_data = data.model_dump(exclude_unset=True)
    if "role" in update_data:
        update_data["role"] = update_data["role"].value
    if "status" in update_data:
        update_data["status"] = update_data["status"].value

    for field, value in update_data.items():
        setattr(assignment, field, value)

    await db.commit()
    await db.refresh(assignment)

    coach_name = await _get_member_name(assignment.coach_id)
    return _assignment_to_response(assignment, coach_name=coach_name)


@router.delete("/{assignment_id}")
async def cancel_assignment(
    assignment_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Cancel a coach assignment (admin only)."""
    result = await db.execute(
        select(CoachAssignment).where(CoachAssignment.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    assignment.status = "cancelled"
    await db.commit()

    return {"detail": "Assignment cancelled"}


# ── Shadow Evaluation Endpoints ──


@router.post("/{assignment_id}/evaluations", response_model=ShadowEvaluationResponse)
async def create_shadow_evaluation(
    assignment_id: uuid.UUID,
    data: ShadowEvaluationCreate,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a shadow evaluation for an assignment (lead coach only)."""
    # Verify assignment exists and is a shadow assignment
    result = await db.execute(
        select(CoachAssignment).where(CoachAssignment.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    if assignment.role != "shadow":
        raise HTTPException(
            status_code=400,
            detail="Evaluations can only be created for shadow assignments",
        )

    evaluator_id = await _get_member_id_from_auth(current_user.user_id)
    if not evaluator_id:
        raise HTTPException(status_code=404, detail="Evaluator member not found")

    evaluation = ShadowEvaluation(
        assignment_id=assignment_id,
        evaluator_id=evaluator_id,
        session_date=data.session_date,
        scores=data.scores,
        feedback=data.feedback,
        recommendation=data.recommendation.value,
    )
    db.add(evaluation)
    await db.commit()
    await db.refresh(evaluation)

    evaluator_name = await _get_member_name(evaluator_id)

    return ShadowEvaluationResponse(
        id=str(evaluation.id),
        assignment_id=str(evaluation.assignment_id),
        evaluator_id=str(evaluation.evaluator_id),
        session_date=evaluation.session_date,
        scores=evaluation.scores,
        feedback=evaluation.feedback,
        recommendation=evaluation.recommendation,
        created_at=evaluation.created_at,
        evaluator_name=evaluator_name,
    )


@router.get(
    "/{assignment_id}/evaluations", response_model=list[ShadowEvaluationResponse]
)
async def list_evaluations(
    assignment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List evaluations for a specific assignment."""
    result = await db.execute(
        select(ShadowEvaluation)
        .where(ShadowEvaluation.assignment_id == assignment_id)
        .order_by(ShadowEvaluation.session_date.desc())
    )
    evaluations = result.scalars().all()

    responses = []
    for e in evaluations:
        evaluator_name = await _get_member_name(e.evaluator_id)
        responses.append(
            ShadowEvaluationResponse(
                id=str(e.id),
                assignment_id=str(e.assignment_id),
                evaluator_id=str(e.evaluator_id),
                session_date=e.session_date,
                scores=e.scores,
                feedback=e.feedback,
                recommendation=e.recommendation,
                created_at=e.created_at,
                evaluator_name=evaluator_name,
            )
        )
    return responses


# ── Readiness Endpoint ──


@router.get("/readiness/{coach_id}", response_model=CoachReadinessResponse)
async def get_coach_readiness(
    coach_id: uuid.UUID,
    target_grade: str = "grade_1",
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get computed readiness assessment for a coach toward a target grade.

    Checks requirements based on target grade:
    - Grade 1: >=5 shadow sessions with passing evals, agreement signed, CPR current, BG check
    - Grade 2: Grade 1 + >=50 hours, >=3 cohorts completed, rating >=4.0
    - Grade 3: Grade 2 + >=200 hours, >=10 lead cohorts, rating >=4.3
    """
    coach_name = await _get_member_name(coach_id)
    checks: list[ReadinessCheckItem] = []
    missing: list[str] = []
    recommendations: list[str] = []

    # --- Check: Coach profile exists (via members-service) ---
    profile = await get_coach_readiness_data(str(coach_id), calling_service="academy")

    if not profile:
        return CoachReadinessResponse(
            coach_id=str(coach_id),
            coach_name=coach_name,
            target_grade=target_grade,
            is_ready=False,
            checks=[],
            missing_requirements=["No coach profile found"],
            recommendations=["Ensure coach has a profile before assessing readiness"],
        )

    hours = profile.get("total_coaching_hours") or 0
    rating = float(profile.get("average_rating") or 0)
    bg_status = profile.get("background_check_status") or ""
    cpr_training = profile.get("has_cpr_training") or False
    cpr_expiry_str = profile.get("cpr_expiry_date")
    if cpr_expiry_str:
        from datetime import datetime as dt

        cpr_expiry = dt.fromisoformat(cpr_expiry_str)
        cpr_valid = cpr_expiry >= datetime.now(timezone.utc)
    else:
        cpr_valid = True
    cpr = bool(cpr_training and cpr_valid)

    # --- Check: Agreement signed (returned from members-service) ---
    has_agreement = profile.get("has_active_agreement", False)
    checks.append(
        ReadinessCheckItem(
            name="Agreement Signed",
            description="Coach has signed the current agreement",
            status=(
                ReadinessCheckStatus.PASSED
                if has_agreement
                else ReadinessCheckStatus.PENDING
            ),
            required=True,
        )
    )
    if not has_agreement:
        missing.append("Sign the coach agreement")

    # --- Check: Background check ---
    bg_passed = bg_status == "approved"
    checks.append(
        ReadinessCheckItem(
            name="Background Check",
            description="Background check completed and approved",
            status=(
                ReadinessCheckStatus.PASSED
                if bg_passed
                else ReadinessCheckStatus.PENDING
            ),
            required=True,
        )
    )
    if not bg_passed:
        missing.append("Complete background check")

    # --- Check: CPR Certification ---
    checks.append(
        ReadinessCheckItem(
            name="CPR Certification",
            description="Current CPR certification on file",
            status=ReadinessCheckStatus.PASSED if cpr else ReadinessCheckStatus.PENDING,
            required=True,
        )
    )
    if not cpr:
        missing.append("Obtain CPR certification")

    # --- Check: Shadow sessions (Grade 1+) ---
    shadow_result = await db.execute(
        select(ShadowEvaluation)
        .join(CoachAssignment)
        .where(
            CoachAssignment.coach_id == coach_id,
            CoachAssignment.role == "shadow",
        )
    )
    shadow_evals = shadow_result.scalars().all()
    passing_evals = [
        e
        for e in shadow_evals
        if e.recommendation in ("ready_for_assistant", "ready_for_lead")
    ]

    shadow_required = 5
    checks.append(
        ReadinessCheckItem(
            name="Shadow Sessions",
            description=f"Complete {shadow_required} shadow sessions with passing evaluations",
            status=(
                ReadinessCheckStatus.PASSED
                if len(passing_evals) >= shadow_required
                else ReadinessCheckStatus.PENDING
            ),
            required=True,
            details=f"{len(passing_evals)}/{shadow_required} passing evaluations",
        )
    )
    if len(passing_evals) < shadow_required:
        missing.append(
            f"Complete {shadow_required - len(passing_evals)} more shadow sessions with passing evaluations"
        )

    # --- Grade 2+ checks ---
    if target_grade in ("grade_2", "grade_3"):
        # Hours check
        hours_required = 50
        checks.append(
            ReadinessCheckItem(
                name="Coaching Hours",
                description=f"Log at least {hours_required} coaching hours",
                status=(
                    ReadinessCheckStatus.PASSED
                    if hours >= hours_required
                    else ReadinessCheckStatus.PENDING
                ),
                required=True,
                details=f"{hours}/{hours_required} hours logged",
            )
        )
        if hours < hours_required:
            missing.append(f"Log {hours_required - hours} more coaching hours")

        # Completed cohorts (coach_assignments is our own table)
        completed_result = await db.execute(
            select(func.count(CoachAssignment.id)).where(
                CoachAssignment.coach_id == coach_id,
                CoachAssignment.role.in_(["lead", "assistant"]),
                CoachAssignment.status == "completed",
            )
        )
        completed_cohorts = completed_result.scalar() or 0
        cohorts_required = 3
        checks.append(
            ReadinessCheckItem(
                name="Completed Cohorts",
                description=f"Complete at least {cohorts_required} cohorts as lead or assistant",
                status=(
                    ReadinessCheckStatus.PASSED
                    if completed_cohorts >= cohorts_required
                    else ReadinessCheckStatus.PENDING
                ),
                required=True,
                details=f"{completed_cohorts}/{cohorts_required} cohorts completed",
            )
        )
        if completed_cohorts < cohorts_required:
            missing.append(
                f"Complete {cohorts_required - completed_cohorts} more cohorts"
            )

        # Rating check
        rating_required = 4.0
        checks.append(
            ReadinessCheckItem(
                name="Coach Rating",
                description=f"Maintain a rating of at least {rating_required}",
                status=(
                    ReadinessCheckStatus.PASSED
                    if rating >= rating_required
                    else ReadinessCheckStatus.PENDING
                ),
                required=True,
                details=f"Current rating: {rating:.1f}",
            )
        )
        if rating < rating_required:
            missing.append(
                f"Improve rating to {rating_required} (currently {rating:.1f})"
            )

    # --- Grade 3 checks ---
    if target_grade == "grade_3":
        # Higher hours
        hours_required_g3 = 200
        checks.append(
            ReadinessCheckItem(
                name="Advanced Hours",
                description=f"Log at least {hours_required_g3} coaching hours",
                status=(
                    ReadinessCheckStatus.PASSED
                    if hours >= hours_required_g3
                    else ReadinessCheckStatus.PENDING
                ),
                required=True,
                details=f"{hours}/{hours_required_g3} hours logged",
            )
        )
        if hours < hours_required_g3:
            missing.append(
                f"Log {hours_required_g3 - hours} more coaching hours for Grade 3"
            )

        # Lead cohorts (coach_assignments is our own table)
        lead_result = await db.execute(
            select(func.count(CoachAssignment.id)).where(
                CoachAssignment.coach_id == coach_id,
                CoachAssignment.role == "lead",
                CoachAssignment.status == "completed",
            )
        )
        lead_cohorts = lead_result.scalar() or 0
        lead_required = 10
        checks.append(
            ReadinessCheckItem(
                name="Lead Cohorts",
                description=f"Complete at least {lead_required} cohorts as lead coach",
                status=(
                    ReadinessCheckStatus.PASSED
                    if lead_cohorts >= lead_required
                    else ReadinessCheckStatus.PENDING
                ),
                required=True,
                details=f"{lead_cohorts}/{lead_required} lead cohorts completed",
            )
        )
        if lead_cohorts < lead_required:
            missing.append(
                f"Complete {lead_required - lead_cohorts} more cohorts as lead"
            )

        # Higher rating
        rating_required_g3 = 4.3
        checks.append(
            ReadinessCheckItem(
                name="Advanced Rating",
                description=f"Maintain a rating of at least {rating_required_g3}",
                status=(
                    ReadinessCheckStatus.PASSED
                    if rating >= rating_required_g3
                    else ReadinessCheckStatus.PENDING
                ),
                required=True,
                details=f"Current rating: {rating:.1f}",
            )
        )
        if rating < rating_required_g3:
            missing.append(f"Improve rating to {rating_required_g3} for Grade 3")

    # Compute overall readiness
    is_ready = len(missing) == 0

    if not is_ready:
        recommendations.append(
            "Focus on completing the missing requirements listed above."
        )
        if not has_agreement:
            recommendations.append("Start by signing the coach agreement.")
        if len(passing_evals) < shadow_required:
            recommendations.append(
                "Request more shadow assignments to gain experience."
            )

    return CoachReadinessResponse(
        coach_id=str(coach_id),
        coach_name=coach_name,
        target_grade=target_grade,
        is_ready=is_ready,
        checks=checks,
        missing_requirements=missing,
        recommendations=recommendations,
    )
