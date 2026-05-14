"""Admin enrollment CRUD: enroll / list / get / update."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import dispatch_notification, get_member_by_id
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    Enrollment,
    EnrollmentStatus,
    PaymentStatus,
    Program,
)
from services.academy_service.routers._shared import (
    _resolve_enrollment_total_fee,
    _sync_installment_state_for_enrollment,
)
from services.academy_service.schemas import (
    EnrollmentCreate,
    EnrollmentResponse,
    EnrollmentUpdate,
)
from services.academy_service.services.chat_sync import (
    ensure_cohort_channel,
    reconcile_cohort_membership,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = get_logger(__name__)
router = APIRouter(tags=["academy"])


@router.post("/enrollments", response_model=EnrollmentResponse)
async def enroll_student(
    enrollment_in: EnrollmentCreate,
    current_user: AuthUser = Depends(require_admin),  # Admin can enroll anyone
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Enrollment).where(
        Enrollment.member_id == enrollment_in.member_id,
        Enrollment.program_id == enrollment_in.program_id,
    )
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=400, detail="Member already enrolled in this cohort"
        )

    program = (
        await db.execute(select(Program).where(Program.id == enrollment_in.program_id))
    ).scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    cohort = None
    if enrollment_in.cohort_id:
        cohort = (
            await db.execute(select(Cohort).where(Cohort.id == enrollment_in.cohort_id))
        ).scalar_one_or_none()
        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found")
        if cohort.program_id != enrollment_in.program_id:
            raise HTTPException(
                status_code=400, detail="Cohort does not belong to the selected program"
            )

        # Admin force-enroll is intentionally allowed past capacity (overrides),
        # but log it so the audit trail captures every over-capacity action.
        # Members hitting POST /enrollments/me are waitlisted instead.
        enrolled_count_result = await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.cohort_id == cohort.id,
                Enrollment.status.in_(
                    [EnrollmentStatus.ENROLLED, EnrollmentStatus.PENDING_APPROVAL]
                ),
            )
        )
        enrolled_count = enrolled_count_result.scalar_one()
        if cohort.capacity is not None and enrolled_count >= cohort.capacity:
            logger.warning(
                "Admin enrollment exceeds cohort capacity: cohort=%s capacity=%s "
                "enrolled=%s admin_auth_id=%s member_id=%s",
                cohort.id,
                cohort.capacity,
                enrolled_count,
                current_user.user_id,
                enrollment_in.member_id,
            )

    price_snapshot = _resolve_enrollment_total_fee(program, cohort)
    enrollment = Enrollment(
        program_id=enrollment_in.program_id,
        cohort_id=enrollment_in.cohort_id,
        member_id=enrollment_in.member_id,
        status=EnrollmentStatus.ENROLLED,  # Admin enrolls directly
        payment_status=PaymentStatus.PENDING,
        uses_installments=False,
        preferences=enrollment_in.preferences,
        price_snapshot_amount=price_snapshot,
        currency_snapshot=program.currency or "NGN",
    )
    db.add(enrollment)

    await db.flush()
    await _sync_installment_state_for_enrollment(db, enrollment)

    await db.commit()

    refreshed = await db.execute(
        select(Enrollment)
        .where(Enrollment.id == enrollment.id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
            selectinload(Enrollment.progress_records),
        )
    )
    enrolled = refreshed.scalar_one()

    # Best-effort: dispatch in-app notification to the enrolled student
    cohort_label = f" ({cohort.name})" if cohort else ""
    await dispatch_notification(
        type="enrollment_confirmed",
        category="academy",
        member_ids=[str(enrollment_in.member_id)],
        title=f"Enrolled: {program.name}{cohort_label}",
        body=f"You have been enrolled in {program.name}{cohort_label}.",
        action_url="/account/academy",
        icon="graduation-cap",
        metadata={
            "enrollment_id": str(enrollment.id),
            "program_id": str(program.id),
            "cohort_id": str(cohort.id) if cohort else None,
        },
        calling_service="academy",
    )

    # Reconcile chat membership in the cohort channel. Best-effort — chat
    # downtime never blocks enrollment. Channel is provisioned at cohort
    # create time; we ensure here too in case the cohort predates chat.
    if cohort is not None:
        await ensure_cohort_channel(
            cohort_id=cohort.id,
            cohort_name=cohort.name,
            created_by_member_id=cohort.coach_id,
        )
        await reconcile_cohort_membership(
            cohort_id=cohort.id,
            member_id=enrollment_in.member_id,
            enrollment_id=enrollment.id,
            action="add",
        )

    return enrolled


@router.get("/enrollments", response_model=List[EnrollmentResponse])
async def list_enrollments(
    status: Optional[EnrollmentStatus] = None,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all enrollments (admin only). Filter by status optional."""

    query = (
        select(Enrollment)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
            selectinload(Enrollment.progress_records),
        )
        .order_by(Enrollment.created_at.desc())
    )

    if status:
        query = query.where(Enrollment.status == status)

    result = await db.execute(query)
    enrollments = result.scalars().all()
    for enrollment in enrollments:
        await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()
    return enrollments


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def get_enrollment(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get detailed enrollment info. Accessible by admins and coaches."""

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

    await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()

    # Enrich with member name/email
    data = EnrollmentResponse.model_validate(enrollment)
    try:
        member_data = await get_member_by_id(
            str(enrollment.member_id), calling_service="academy"
        )
        if member_data:
            first_name = member_data.get("first_name", "")
            last_name = member_data.get("last_name", "")
            data.member_name = f"{first_name} {last_name}".strip() or None
            data.member_email = member_data.get("email")
    except Exception:
        pass

    return data


@router.patch("/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def update_enrollment(
    enrollment_id: uuid.UUID,
    enrollment_in: EnrollmentUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update enrollment status and/or payment status."""

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

    update_data = enrollment_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(enrollment, field, value)

    await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()

    # Reload with relationships eager loaded to avoid lazy-load during response serialization
    refreshed = await db.execute(query)
    enrollment = refreshed.scalar_one_or_none()
    return enrollment
