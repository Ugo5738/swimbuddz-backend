"""Member self-enrollment endpoint (POST /enrollments/me)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.service_client import dispatch_notification, get_member_by_auth_id
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    CohortStatus,
    Enrollment,
    EnrollmentStatus,
    PaymentStatus,
    Program,
)
from services.academy_service.routers._shared import (
    _resolve_enrollment_total_fee,
    _sync_installment_state_for_enrollment,
)
from services.academy_service.schemas import EnrollmentResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(tags=["academy"])


@router.post("/enrollments/me", response_model=EnrollmentResponse)
async def self_enroll(
    request_data: dict,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Allow a member to request enrollment in a program or specific cohort.
    If 'program_id' only: Creates a PENDING_APPROVAL request.
    If 'cohort_id': Validates cohort and creates PENDING_APPROVAL request.
    """
    program_id_str = request_data.get("program_id")
    cohort_id_str = request_data.get("cohort_id")
    preferences = request_data.get("preferences")

    if not program_id_str and not cohort_id_str:
        raise HTTPException(
            status_code=422, detail="Either program_id or cohort_id is required"
        )

    program_id = uuid.UUID(program_id_str) if program_id_str else None
    cohort_id = uuid.UUID(cohort_id_str) if cohort_id_str else None
    program = None
    cohort = None

    # 1. Get Member ID via members-service
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )
    if not member:
        raise HTTPException(
            status_code=404,
            detail="Member profile not found. Please complete registration.",
        )

    # 2. Derive/Validate Program ID
    if cohort_id:
        cohort_query = select(Cohort).where(Cohort.id == cohort_id)
        cohort_result = await db.execute(cohort_query)
        cohort = cohort_result.scalar_one_or_none()

        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found")

        # If user picked a cohort, ensure we set the program_id correctly
        if program_id and program_id != cohort.program_id:
            raise HTTPException(
                status_code=400,
                detail="Cohort does not belong to the specified program",
            )
        program_id = cohort.program_id

        # Check if the cohort's program is published
        program_query = select(Program).where(Program.id == cohort.program_id)
        program_result = await db.execute(program_query)
        program = program_result.scalar_one_or_none()

        if not program or not program.is_published:
            raise HTTPException(
                status_code=400,
                detail="This program is not yet available for enrollment.",
            )

        # Validation for mid-entry if cohort is ACTIVE
        if cohort.status == CohortStatus.ACTIVE:
            # Check if mid-entry is allowed for this cohort
            if not cohort.allow_mid_entry:
                raise HTTPException(
                    status_code=400,
                    detail="This cohort does not allow mid-entry. Please join a waitlist or select another cohort.",
                )

            # Check if within cutoff window
            now = utc_now()
            days_since_start = (now - cohort.start_date).days
            current_week = (days_since_start // 7) + 1

            if current_week > cohort.mid_entry_cutoff_week:
                raise HTTPException(
                    status_code=400,
                    detail=f"Mid-entry window has closed (week {current_week} > cutoff week {cohort.mid_entry_cutoff_week}). Please join a waitlist or select another cohort.",
                )

    elif program_id:
        # User is requesting to join a generic Program (Request-based)
        program_query = select(Program).where(Program.id == program_id)
        program_result = await db.execute(program_query)
        program = program_result.scalar_one_or_none()
        if not program:
            raise HTTPException(status_code=404, detail="Program not found")

        # Check if program is published
        if not program.is_published:
            raise HTTPException(
                status_code=400,
                detail="This program is not yet available for enrollment.",
            )

    # 3. Check Existing Enrollment/Request
    # Only block enrolling in the SAME COHORT twice.
    # Allow:
    #   - Different cohorts in same program (e.g., future cohorts)
    #   - Different programs simultaneously
    if cohort_id:
        query = select(Enrollment).where(
            Enrollment.member_id == member["id"],
            Enrollment.cohort_id == cohort_id,  # Only check same cohort, not program
            Enrollment.status.in_(
                [EnrollmentStatus.ENROLLED, EnrollmentStatus.PENDING_APPROVAL]
            ),
        )
        result = await db.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            detail = (
                "You are already enrolled in this cohort"
                if existing.status == EnrollmentStatus.ENROLLED
                else "You already have a pending request for this cohort"
            )
            raise HTTPException(status_code=400, detail=detail)

    # 4. Check Capacity and Determine Status
    # If cohort is at capacity, set status to WAITLIST instead of PENDING_APPROVAL
    enrollment_status = EnrollmentStatus.PENDING_APPROVAL

    if cohort_id and cohort:
        # Count current enrollments (ENROLLED + PENDING_APPROVAL)

        enrolled_count_result = await db.execute(
            select(func.count(Enrollment.id)).where(
                Enrollment.cohort_id == cohort_id,
                Enrollment.status.in_(
                    [EnrollmentStatus.ENROLLED, EnrollmentStatus.PENDING_APPROVAL]
                ),
            )
        )
        enrolled_count = enrolled_count_result.scalar() or 0

        if enrolled_count >= cohort.capacity:
            # Cohort is at capacity - add to waitlist
            enrollment_status = EnrollmentStatus.WAITLIST

    # 5. Create Enrollment Request
    # Status is PENDING_APPROVAL by default, or WAITLIST if at capacity
    # Payment status handles the financial part separate from Admission.
    enrollment = Enrollment(
        program_id=program_id,
        cohort_id=cohort_id,  # Can be None
        member_id=member["id"],
        member_auth_id=current_user.user_id,  # For decoupled ownership verification
        status=enrollment_status,
        payment_status=PaymentStatus.PENDING,
        uses_installments=False,
        preferences=preferences or {},
        price_snapshot_amount=(
            _resolve_enrollment_total_fee(program, cohort)
            if (program and cohort)
            else None
        ),
        currency_snapshot=(program.currency if program else None),
    )
    db.add(enrollment)

    await db.flush()
    await _sync_installment_state_for_enrollment(db, enrollment)

    await db.commit()

    # Re-fetch with relationships to avoid lazy loading issues
    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment.id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
            selectinload(Enrollment.progress_records),
        )
    )
    result = await db.execute(query)
    enrolled = result.scalar_one()

    # Best-effort: dispatch in-app notification
    if enrollment_status == EnrollmentStatus.WAITLIST:
        notif_title = f"Waitlisted: {program.name}" if program else "Waitlisted"
        notif_body = (
            "You've been added to the waitlist. We'll notify you when a spot opens."
        )
        notif_type = "enrollment_waitlisted"
    else:
        notif_title = (
            f"Enrollment Pending: {program.name}" if program else "Enrollment Pending"
        )
        notif_body = (
            "Your enrollment request has been submitted and is pending approval."
        )
        notif_type = "enrollment_pending"

    await dispatch_notification(
        type=notif_type,
        category="academy",
        member_ids=[str(member["id"])],
        title=notif_title,
        body=notif_body,
        action_url="/account/academy",
        icon="graduation-cap",
        metadata={
            "enrollment_id": str(enrollment.id),
            "program_id": str(program_id) if program_id else None,
            "cohort_id": str(cohort_id) if cohort_id else None,
        },
        calling_service="academy",
    )

    return enrolled
