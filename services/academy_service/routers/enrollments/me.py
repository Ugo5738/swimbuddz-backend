"""Member-facing endpoints: list my enrollments, waitlist position, withdraw."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_id
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    Enrollment,
    EnrollmentStatus,
    InstallmentStatus,
)
from services.academy_service.routers._shared import (
    _resolve_enrollment_total_fee,
    _sync_installment_state_for_enrollment,
    compute_withdrawal_refund,
)
from services.academy_service.schemas import (
    EnrollmentResponse,
    WithdrawEnrollmentRequest,
    WithdrawEnrollmentResponse,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ._helpers import _annotate_payment_with_refund, _recompute_member_academy_until

logger = get_logger(__name__)
router = APIRouter(tags=["academy"])


@router.get("/my-enrollments", response_model=List[EnrollmentResponse])
async def get_my_enrollments(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get all enrollments for the current user."""

    query = (
        select(Enrollment)
        .where(Enrollment.member_auth_id == current_user.user_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
            selectinload(Enrollment.progress_records),
        )
    )
    result = await db.execute(query)
    enrollments = result.scalars().all()
    for enrollment in enrollments:
        await _sync_installment_state_for_enrollment(db, enrollment)
    await db.commit()
    return enrollments


@router.get("/my-enrollments/{enrollment_id}/waitlist-position")
async def get_my_waitlist_position(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get waitlist position for a waitlisted enrollment."""

    # Get the enrollment
    query = select(Enrollment).where(
        Enrollment.id == enrollment_id,
        Enrollment.member_auth_id == current_user.user_id,
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    if enrollment.status != EnrollmentStatus.WAITLIST:
        return {"position": None, "message": "Not on waitlist"}

    if not enrollment.cohort_id:
        return {"position": None, "message": "No cohort assigned"}

    # Count waitlist entries created before this one (position = count + 1)
    position_result = await db.execute(
        select(func.count(Enrollment.id)).where(
            Enrollment.cohort_id == enrollment.cohort_id,
            Enrollment.status == EnrollmentStatus.WAITLIST,
            Enrollment.created_at < enrollment.created_at,
        )
    )
    position = (position_result.scalar() or 0) + 1

    return {
        "enrollment_id": str(enrollment_id),
        "cohort_id": str(enrollment.cohort_id),
        "position": position,
        "status": enrollment.status.value,
    }


@router.get("/my-enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def get_my_enrollment(
    enrollment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a specific enrollment for the current member."""

    query = (
        select(Enrollment)
        .where(
            Enrollment.id == enrollment_id,
            Enrollment.member_auth_id == current_user.user_id,
        )
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
    return enrollment


@router.post(
    "/my-enrollments/{enrollment_id}/withdraw",
    response_model=WithdrawEnrollmentResponse,
)
async def withdraw_my_enrollment(
    enrollment_id: uuid.UUID,
    payload: WithdrawEnrollmentRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Member-initiated voluntary withdrawal from a cohort.

    Refund policy (docs/club/PRICING_STRATEGY.md, founder-confirmed May 2026):
      - Before cohort start: 90% refund of paid amount.
      - In mid-entry window (week 1 → mid_entry_cutoff_week): 50% of unused
        prorated portion, capped at paid amount.
      - After cutoff: no refund.

    In all cases:
      - All unpaid installments are WAIVED.
      - Enrollment status → DROPPED, dropped_at = now.
      - Member's academy_paid_until is recomputed from remaining ENROLLED
        cohorts (multi-cohort safe). The post-academy free club month is
        only granted at natural graduation (via the cron), NOT on withdrawal.
      - Community membership and any pre-existing club access are preserved.

    The refund itself is queued for admin disbursement — Paystack refund API
    is not invoked from this endpoint (Nigerian payment flows are commonly
    settled via direct transfer). The refund obligation is written to the
    relevant payments' metadata as ``refund_owed``.
    """
    query = (
        select(Enrollment)
        .where(
            Enrollment.id == enrollment_id,
            Enrollment.member_auth_id == current_user.user_id,
        )
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    if enrollment.status in (EnrollmentStatus.DROPPED, EnrollmentStatus.GRADUATED):
        raise HTTPException(
            status_code=400,
            detail=f"Enrollment is already in terminal state: {enrollment.status.value}",
        )

    cohort = enrollment.cohort
    program = enrollment.program
    if cohort is None or program is None:
        raise HTTPException(
            status_code=400,
            detail="Enrollment is missing cohort/program — cannot compute refund",
        )

    now = utc_now()
    cohort_start = cohort.start_date
    if cohort_start.tzinfo is None:
        cohort_start = cohort_start.replace(tzinfo=__import__("datetime").timezone.utc)

    # Sum paid installments (kobo)
    installments = enrollment.installments or []
    paid_kobo = sum(
        i.amount for i in installments if i.status == InstallmentStatus.PAID
    )
    program_fee_kobo = (
        enrollment.price_snapshot_amount
        or _resolve_enrollment_total_fee(program, cohort)
        or 0
    )

    window, refund_kobo, refund_percent = compute_withdrawal_refund(
        now=now,
        cohort_start=cohort_start,
        duration_weeks=int(program.duration_weeks or 12),
        mid_entry_cutoff_week=int(cohort.mid_entry_cutoff_week or 2),
        total_paid_kobo=paid_kobo,
        program_fee_kobo=program_fee_kobo,
    )

    # Waive all unpaid installments
    waived_count = 0
    for inst in installments:
        if inst.status == InstallmentStatus.PENDING:
            inst.status = InstallmentStatus.WAIVED
            waived_count += 1

    # Flip enrollment to DROPPED with withdrawal anchor
    enrollment.status = EnrollmentStatus.DROPPED
    enrollment.dropped_at = now
    enrollment.access_suspended = True

    # Annotate the paid payments with refund obligation. We split the refund
    # across paid installments proportionally so each payment record shows
    # what's owed against it — admins can reconcile via the payment list.
    payment_refs: list[str] = []
    if refund_kobo > 0 and paid_kobo > 0:
        remaining_refund_kobo = refund_kobo
        paid_installments = [
            i for i in installments if i.status == InstallmentStatus.PAID
        ]
        for idx, inst in enumerate(paid_installments):
            if not inst.payment_reference:
                continue
            payment_refs.append(inst.payment_reference)
            # Last installment absorbs any rounding remainder
            if idx == len(paid_installments) - 1:
                share_kobo = remaining_refund_kobo
            else:
                share_kobo = int(round(refund_kobo * (inst.amount / paid_kobo)))
                share_kobo = min(share_kobo, remaining_refund_kobo)
            remaining_refund_kobo -= share_kobo
            await _annotate_payment_with_refund(
                payment_reference=inst.payment_reference,
                refund_kobo=share_kobo,
                enrollment_id=str(enrollment.id),
                window=window,
                reason=payload.reason,
                calling_service="academy",
            )

    # Recompute academy_paid_until from remaining ENROLLED cohorts
    await _recompute_member_academy_until(
        member_auth_id=current_user.user_id,
        member_id=enrollment.member_id,
        db=db,
    )

    await db.commit()

    refund_naira = refund_kobo / 100
    if refund_kobo > 0:
        refund_note = (
            f"Refund of ₦{refund_naira:,.2f} owed (paid via {', '.join(payment_refs)}). "
            f"Admin: disburse via original payment channel."
        )
    else:
        refund_note = (
            f"No refund per policy ({window}). All unpaid installments waived."
        )

    # Notify the member that the withdrawal succeeded + admin about any
    # refund obligation. Best-effort: don't block the withdrawal response
    # if mail delivery fails — the obligation is already recorded on the
    # payment(s) and can be processed from the admin refund queue.
    try:
        program_name = program.name if program else "Academy Program"
        member_data = await get_member_by_id(
            str(enrollment.member_id), calling_service="academy"
        )
        member_email = member_data.get("email") if member_data else None
        member_first = (
            member_data.get("first_name", "Member") if member_data else "Member"
        )
        member_full = (
            f"{member_data.get('first_name', '')} {member_data.get('last_name', '')}".strip()
            if member_data
            else "Member"
        )

        email_client = get_email_client()

        if member_email:
            await email_client.send_template(
                template_type="withdrawal_confirmation",
                to_email=member_email,
                template_data={
                    "member_name": member_first,
                    "program_name": program_name,
                    "cohort_name": cohort.name,
                    "window": window,
                    "refund_naira": refund_naira,
                    "waived_installment_count": waived_count,
                    "refund_note": refund_note,
                },
            )

        if refund_kobo > 0:
            _settings = get_settings()
            await email_client.send_template(
                template_type="admin_refund_owed",
                to_email=_settings.ADMIN_EMAIL,
                template_data={
                    "member_name": member_full,
                    "member_email": member_email or "",
                    "program_name": program_name,
                    "cohort_name": cohort.name,
                    "window": window,
                    "refund_naira": refund_naira,
                    "payment_references": payment_refs,
                    "enrollment_id": str(enrollment.id),
                    "reason": payload.reason,
                },
            )
    except Exception:
        logger.warning(
            "Failed to send withdrawal/refund notification emails for "
            "enrollment %s (best-effort)",
            enrollment.id,
            exc_info=True,
        )

    return WithdrawEnrollmentResponse(
        enrollment_id=enrollment.id,
        status=enrollment.status.value,
        window=window,
        refund_kobo=refund_kobo,
        refund_percent=refund_percent,
        paid_kobo=paid_kobo,
        waived_installment_count=waived_count,
        payment_references=payment_refs,
        refund_note=refund_note,
    )
