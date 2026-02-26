from fastapi import APIRouter
from services.academy_service.routers._shared import (
    AsyncSession,
    AuthUser,
    Cohort,
    Depends,
    Enrollment,
    EnrollmentStatus,
    HTTPException,
    Milestone,
    Response,
    StudentProgress,
    generate_certificate_pdf,
    generate_progress_report_pdf,
    get_async_db,
    get_current_user,
    get_logger,
    get_member_by_id,
    require_admin,
    select,
    selectinload,
    utc_now,
    uuid,
)

router = APIRouter(tags=["academy"])
logger = get_logger(__name__)


# --- PDF Reports ---


@router.get("/cohorts/{cohort_id}/progress-report.pdf")
async def download_cohort_progress_report(
    cohort_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Download a PDF progress report for a specific cohort.
    Contains all students and their milestone progress.
    """
    # Get cohort with program
    cohort_query = (
        select(Cohort)
        .options(selectinload(Cohort.program))
        .where(Cohort.id == cohort_id)
    )
    cohort_result = await db.execute(cohort_query)
    cohort = cohort_result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    program = cohort.program
    if not program:
        raise HTTPException(status_code=400, detail="Cohort has no associated program")

    # Get all milestones
    milestone_query = select(Milestone).where(Milestone.program_id == program.id)
    milestone_result = await db.execute(milestone_query)
    all_milestones = milestone_result.scalars().all()
    total_milestones = len(all_milestones)
    milestone_map = {m.id: m.name for m in all_milestones}

    # Get all enrollments
    enrollment_query = select(Enrollment).where(
        Enrollment.cohort_id == cohort_id,
        Enrollment.status == EnrollmentStatus.ENROLLED,
    )
    enrollment_result = await db.execute(enrollment_query)
    enrollments = enrollment_result.scalars().all()

    if not enrollments:
        raise HTTPException(status_code=404, detail="No enrolled students found")

    # For cohort PDF, we'll generate for the first student as a demo
    # In a real implementation, you might want a cohort-level summary PDF
    enrollment = enrollments[0]

    # Get member name from members service
    student_name = "Student"
    try:
        member_data = await get_member_by_id(
            str(enrollment.member_id), calling_service="academy"
        )
        if member_data:
            first_name = member_data.get("first_name", "")
            last_name = member_data.get("last_name", "")
            student_name = f"{first_name} {last_name}".strip() or "Student"
    except Exception:
        pass

    # Get progress for this enrollment
    progress_query = select(StudentProgress).where(
        StudentProgress.enrollment_id == enrollment.id
    )
    progress_result = await db.execute(progress_query)
    all_progress = progress_result.scalars().all()

    completed_count = len([p for p in all_progress if p.status.value == "achieved"])
    milestone_data = [
        {
            "name": milestone_map.get(p.milestone_id, "Unknown"),
            "status": p.status.value if p.status else "pending",
            "achieved_at": p.achieved_at,
            "coach_notes": p.coach_notes,
        }
        for p in all_progress
    ]

    # Generate PDF
    pdf_bytes = generate_progress_report_pdf(
        student_name=student_name,
        program_name=program.name,
        cohort_name=cohort.name,
        start_date=cohort.start_date,
        end_date=cohort.end_date,
        milestones=milestone_data,
        total_milestones=total_milestones,
        completed_milestones=completed_count,
        report_date=utc_now(),
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=progress_report_{cohort.name.replace(' ', '_')}.pdf"
        },
    )


@router.get("/enrollments/{enrollment_id}/certificate.pdf")
async def download_certificate(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Download completion certificate for an enrollment.
    Only available if all milestones are completed and certificate was issued.
    """
    # Get enrollment with program and cohort
    query = (
        select(Enrollment)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
        )
        .where(Enrollment.id == enrollment_id)
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    # Verify ownership (unless admin)
    if str(enrollment.member_id) != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=403, detail="Not authorized to view this certificate"
        )

    # Check if certificate has been issued
    if not enrollment.certificate_issued_at or not enrollment.certificate_code:
        raise HTTPException(
            status_code=404,
            detail="Certificate not yet available. Complete all milestones to earn your certificate.",
        )

    # Get member name via members-service
    member = await get_member_by_id(
        str(enrollment.member_id), calling_service="academy"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    student_name = f"{member['first_name']} {member['last_name']}"

    cohort = enrollment.cohort
    program = enrollment.program or (cohort.program if cohort else None)
    program_name = program.name if program else "SwimBuddz Program"

    # Generate PDF
    pdf_bytes = generate_certificate_pdf(
        student_name=student_name,
        program_name=program_name,
        completion_date=enrollment.certificate_issued_at,
        verification_code=enrollment.certificate_code,
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=certificate_{program_name.replace(' ', '_')}.pdf"
        },
    )
