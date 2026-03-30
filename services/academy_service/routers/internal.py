from datetime import datetime as _datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel as _BaseModel
from sqlalchemy import func

from services.academy_service.models import StudentProgress
from services.academy_service.routers._shared import (
    AsyncSession,
    AuthUser,
    CoachAssignment,
    Cohort,
    Depends,
    Enrollment,
    EnrollmentResponse,
    EnrollmentStatus,
    HTTPException,
    List,
    _sync_installment_state_for_enrollment,
    get_async_db,
    get_logger,
    require_admin,
    select,
    selectinload,
    uuid,
)

router = APIRouter(prefix="/internal/academy", tags=["internal"])
logger = get_logger(__name__)


# --- Internal Service-to-Service Endpoints ---


@router.get(
    "/coaches/{coach_member_id}/cohort-ids",
    response_model=List[uuid.UUID],
)
async def list_cohort_ids_for_coach(
    coach_member_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Internal helper endpoint used by sessions-service.

    Returns cohort IDs where the given coach is assigned (legacy cohort.coach_id
    or active lead/assistant coach assignments).
    """
    cohort_id_rows = await db.execute(
        select(Cohort.id).where(Cohort.coach_id == coach_member_id)
    )
    legacy_ids = {row[0] for row in cohort_id_rows.fetchall()}

    assignment_rows = await db.execute(
        select(CoachAssignment.cohort_id)
        .where(CoachAssignment.coach_id == coach_member_id)
        .where(CoachAssignment.status == "active")
        .where(CoachAssignment.role.in_(["lead", "assistant"]))
    )
    assigned_ids = {row[0] for row in assignment_rows.fetchall()}

    return sorted(legacy_ids | assigned_ids)


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def get_enrollment_internal(
    enrollment_id: uuid.UUID,
    use_installments: bool = False,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get a single enrollment by ID (internal service-to-service call).
    Used by payments service to lookup enrollment details.
    No auth required as this is called with service role token.

    Pass ``?use_installments=true`` when the member opted for an installment
    plan at checkout — this triggers schedule creation if none exists yet.
    """

    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
            selectinload(Enrollment.progress_records),
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    await _sync_installment_state_for_enrollment(
        db, enrollment, use_installments=use_installments
    )
    await db.commit()
    return enrollment


@router.get("/cohorts/{cohort_id}")
async def get_cohort_internal(
    cohort_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get basic cohort info (internal service-to-service call).
    Used by communications-service to validate coach ownership.
    """
    query = select(Cohort).where(Cohort.id == cohort_id)
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    return {
        "id": str(cohort.id),
        "name": cohort.name,
        "coach_id": str(cohort.coach_id) if cohort.coach_id else None,
        "program_id": str(cohort.program_id) if cohort.program_id else None,
        "status": cohort.status.value if cohort.status else None,
    }


@router.get("/cohorts/{cohort_id}/enrolled-students")
async def get_cohort_enrolled_students_internal(
    cohort_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get enrolled students for a cohort (internal service-to-service call).
    Used by communications-service for messaging.
    Returns enrollment info with member_id (caller fetches member details separately).
    """
    query = select(Enrollment).where(
        Enrollment.cohort_id == cohort_id,
        Enrollment.status.in_(
            [
                EnrollmentStatus.ENROLLED,
            ]
        ),
    )
    result = await db.execute(query)
    enrollments = result.scalars().all()
    return [
        {
            "enrollment_id": str(e.id),
            "member_id": str(e.member_id),
            "status": e.status.value if e.status else None,
        }
        for e in enrollments
    ]


# ---------------------------------------------------------------------------
# Reporting: member academy summary
# ---------------------------------------------------------------------------


class MemberAcademySummary(_BaseModel):
    milestones_achieved: int = 0
    milestones_in_progress: int = 0
    programs_enrolled: int = 0
    certificates_earned: int = 0


@router.get(
    "/member-summary/{member_auth_id}",
    response_model=MemberAcademySummary,
)
async def get_member_academy_summary(
    member_auth_id: str,
    date_from: _datetime = Query(..., alias="from"),
    date_to: _datetime = Query(..., alias="to"),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate academy stats for a member within a date range.

    Used by the reporting service for quarterly reports.
    """

    # Count enrollments in the period
    enrollment_result = await db.execute(
        select(func.count(Enrollment.id)).where(
            Enrollment.member_auth_id == member_auth_id,
            Enrollment.enrolled_at >= date_from,
            Enrollment.enrolled_at <= date_to,
        )
    )
    programs_enrolled = enrollment_result.scalar() or 0

    # Count certificates earned
    cert_result = await db.execute(
        select(func.count(Enrollment.id)).where(
            Enrollment.member_auth_id == member_auth_id,
            Enrollment.certificate_issued_at >= date_from,
            Enrollment.certificate_issued_at <= date_to,
        )
    )
    certificates_earned = cert_result.scalar() or 0

    # Get enrollment IDs for this member
    enrollment_ids_result = await db.execute(
        select(Enrollment.id).where(
            Enrollment.member_auth_id == member_auth_id,
        )
    )
    enrollment_ids = [row[0] for row in enrollment_ids_result.all()]

    milestones_achieved = 0
    milestones_in_progress = 0

    if enrollment_ids:
        # Count milestones achieved in the period
        achieved_result = await db.execute(
            select(func.count(StudentProgress.id)).where(
                StudentProgress.enrollment_id.in_(enrollment_ids),
                StudentProgress.status == "achieved",
                StudentProgress.achieved_at >= date_from,
                StudentProgress.achieved_at <= date_to,
            )
        )
        milestones_achieved = achieved_result.scalar() or 0

        # Count milestones in progress
        in_progress_result = await db.execute(
            select(func.count(StudentProgress.id)).where(
                StudentProgress.enrollment_id.in_(enrollment_ids),
                StudentProgress.status == "pending",
            )
        )
        milestones_in_progress = in_progress_result.scalar() or 0

    return MemberAcademySummary(
        milestones_achieved=milestones_achieved,
        milestones_in_progress=milestones_in_progress,
        programs_enrolled=programs_enrolled,
        certificates_earned=certificates_earned,
    )
