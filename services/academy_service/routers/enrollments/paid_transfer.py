"""Financially reconciled Academy transfers with provenance and progress preservation.

Cash is never booked here; payments owns receipts. This router transfers only
verified tuition value and leaves historical enrollments / attendance intact.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.academy_service.models import (
    AcademyEnrollmentChange,
    AcademyFinancialCredit,
    AcademyTransferRefundObligation,
    Cohort,
    CohortStatus,
    Enrollment,
    EnrollmentStatus,
    InstallmentStatus,
    PaymentStatus,
    Program,
    StudentProgress,
)
from services.academy_service.routers._shared import (
    _resolve_enrollment_total_fee,
    _resolve_enrollment_membership_policy,
    _sync_installment_state_for_enrollment,
)
from services.academy_service.services.installments import (
    apply_member_payment_across_installments,
)
from .change_cohort import financial_state

router = APIRouter()


class ApproveReviewedTransfer(BaseModel):
    reason: str = Field(min_length=20, max_length=2000)
    transferable_credit_kobo: int = Field(ge=0)
    consumed_services_kobo: int = Field(ge=0)
    refund_due_kobo: int = Field(default=0, ge=0)
    refund_reason: str | None = Field(default=None, min_length=20, max_length=1000)
    discount_kobo: int = Field(default=0, ge=0)
    discount_reason: str | None = Field(default=None, min_length=10, max_length=1000)
    confirmed_attendance_review: bool = False

    @model_validator(mode="after")
    def valid_discount(self):
        if self.refund_due_kobo and not self.refund_reason:
            raise ValueError("A refundable tuition surplus requires a written reason")
        if self.discount_kobo and not self.discount_reason:
            raise ValueError("A manual discount requires a written approval reason")
        return self


@router.get("/admin/academy/enrollment-changes/{change_id}/finance-preview")
async def preview_reviewed_transfer(
    change_id: uuid.UUID,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Show authoritative tuition credit, old payments, and destination price."""
    change = (
        await db.execute(
            select(AcademyEnrollmentChange).where(
                AcademyEnrollmentChange.id == change_id,
            )
        )
    ).scalar_one_or_none()
    if not change:
        raise HTTPException(404, "Cohort change not found")
    source = (
        await db.execute(
            select(Enrollment)
            .where(Enrollment.id == change.from_enrollment_id)
            .options(selectinload(Enrollment.progress_records))
        )
    ).scalar_one_or_none()
    target = (
        await db.execute(select(Cohort).where(Cohort.id == change.target_cohort_id))
    ).scalar_one_or_none()
    if not source or not target or not source.program_id:
        raise HTTPException(409, "Enrollment and target must exist")
    programme = (
        await db.execute(select(Program).where(Program.id == source.program_id))
    ).scalar_one_or_none()
    if not programme:
        raise HTTPException(409, "Academy programme missing")
    financial = await financial_state(source.id)
    credits = (
        (
            await db.execute(
                select(AcademyFinancialCredit).where(
                    AcademyFinancialCredit.enrollment_id == source.id,
                    AcademyFinancialCredit.state == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    receipt_credit = sum(credit.amount_kobo for credit in credits)
    verified_cash = int(financial.get("verified_paid_tuition_kobo") or 0)
    return {
        "change_id": str(change.id),
        "source_enrollment_id": str(source.id),
        "member_auth_id": source.member_auth_id,
        "verified_paid_tuition_kobo": verified_cash,
        "verified_allocation_credit_kobo": receipt_credit,
        "verified_total_kobo": verified_cash + receipt_credit,
        "destination_base_tuition_kobo": _resolve_enrollment_total_fee(
            programme, target
        ),
        "recorded_progress_count": len(source.progress_records),
        "eligible": financial.get("paid_transfer_eligible", False),
        "blocked_payment_references": financial.get("blocked_references", []),
        "attempts": financial.get("attempts", []),
    }


@router.post("/admin/academy/enrollment-changes/{change_id}/approve-reviewed")
async def approve_reviewed_transfer(
    change_id: uuid.UUID,
    payload: ApproveReviewedTransfer,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Make one Academy-side atomic transfer of verified prepaid tuition.

    All original payment references remain on the source enrollment. The
    destination gets a single internally credited obligation, not new cash.
    """
    change = (
        await db.execute(
            select(AcademyEnrollmentChange)
            .where(AcademyEnrollmentChange.id == change_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not change:
        raise HTTPException(404, "Change request not found")
    if change.state == "completed" and change.to_enrollment_id:
        return {
            "state": "completed",
            "enrollment_id": str(change.to_enrollment_id),
            "idempotent": True,
        }
    if change.state != "needs_review":
        raise HTTPException(409, "This request is no longer awaiting review")

    source = (
        await db.execute(
            select(Enrollment)
            .where(Enrollment.id == change.from_enrollment_id)
            .options(
                selectinload(Enrollment.installments),
                selectinload(Enrollment.progress_records),
                selectinload(Enrollment.cohort).selectinload(Cohort.program),
                selectinload(Enrollment.program),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not source or source.status not in {
        EnrollmentStatus.PENDING_APPROVAL,
        EnrollmentStatus.ENROLLED,
        EnrollmentStatus.WAITLIST,
    }:
        raise HTTPException(409, "The original enrollment is not eligible for transfer")
    if not source.program_id or not source.member_auth_id:
        raise HTTPException(409, "Original learner and programme must be verified")

    # The payments owner checks for paid vs pending/proof/rejected/refunded
    # attempts and returns only tuition (never membership or PSP charges).
    financial = await financial_state(source.id)
    if not financial.get("paid_transfer_eligible", False):
        raise HTTPException(
            409,
            "Unresolved payment, proof, refund or checkout attempts require finance reconciliation first",
        )
    original_references = {
        reference
        for reference in [
            source.payment_reference,
            *(item.payment_reference for item in source.installments),
        ]
        if reference
        and not reference.startswith("academy-receipt-allocation:")
        and not reference.startswith("cohort-change:")
    }
    if not original_references.issubset(set(financial.get("references") or [])):
        raise HTTPException(
            409,
            "Recorded historical payment references could not be verified by Payments",
        )
    active_credits = (
        (
            await db.execute(
                select(AcademyFinancialCredit)
                .where(
                    AcademyFinancialCredit.enrollment_id == source.id,
                    AcademyFinancialCredit.state == "active",
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    verified_total = int(financial.get("verified_paid_tuition_kobo") or 0) + sum(
        item.amount_kobo for item in active_credits
    )
    if (
        payload.transferable_credit_kobo
        + payload.consumed_services_kobo
        + payload.refund_due_kobo
        != verified_total
    ):
        raise HTTPException(
            409,
            "Verified tuition must equal destination credit plus consumed services plus a recorded refund liability",
        )
    if (source.progress_records or payload.consumed_services_kobo > 0) and not payload.confirmed_attendance_review:
        raise HTTPException(
            409,
            "Confirm the attendance and milestone review before transferring progress",
        )

    target = (
        await db.execute(
            select(Cohort).where(Cohort.id == change.target_cohort_id).with_for_update()
        )
    ).scalar_one_or_none()
    if (
        not target
        or target.program_id != source.program_id
        or target.status not in {CohortStatus.OPEN, CohortStatus.ACTIVE}
    ):
        raise HTTPException(409, "The destination cohort is not available")
    now = utc_now()
    if target.status == CohortStatus.ACTIVE:
        week = max(1, ((now - target.start_date).days // 7) + 1)
        if not target.allow_mid_entry or week > target.mid_entry_cutoff_week:
            raise HTTPException(409, "The destination mid-entry cutoff has passed")
    programme = (
        await db.execute(select(Program).where(Program.id == source.program_id))
    ).scalar_one_or_none()
    if not programme or not programme.is_published:
        raise HTTPException(409, "Academy programme is unavailable")

    other = (
        await db.execute(
            select(Enrollment.id).where(
                Enrollment.member_id == source.member_id,
                Enrollment.cohort_id == target.id,
                Enrollment.status.in_(
                    [
                        EnrollmentStatus.PENDING_APPROVAL,
                        EnrollmentStatus.ENROLLED,
                        EnrollmentStatus.WAITLIST,
                    ]
                ),
            )
        )
    ).scalar_one_or_none()
    if other:
        raise HTTPException(409, "Learner already has an active destination placement")
    count = (
        await db.execute(
            select(func.count(Enrollment.id)).where(
                Enrollment.cohort_id == target.id,
                Enrollment.status.in_(
                    [
                        EnrollmentStatus.PENDING_APPROVAL,
                        EnrollmentStatus.ENROLLED,
                    ]
                ),
            )
        )
    ).scalar_one()
    if target.capacity is not None and count >= target.capacity:
        raise HTTPException(409, "The destination cohort is full")

    base_fee_kobo = _resolve_enrollment_total_fee(programme, target)
    new_fee_kobo = base_fee_kobo - payload.discount_kobo
    if new_fee_kobo < 0:
        raise HTTPException(409, "Approved discount exceeds cohort tuition")
    if payload.transferable_credit_kobo > new_fee_kobo:
        raise HTTPException(
            409,
            "Credit exceeds new tuition; record and settle the refundable surplus before this transfer",
        )

    # Historical source enrollments and their source receipts remain retained.
    # The source is dropped first to prevent duplicate member access.
    source.status = EnrollmentStatus.DROPPED
    source.dropped_at = now
    source.access_suspended = True
    new_enrollment = Enrollment(
        member_id=source.member_id,
        member_auth_id=source.member_auth_id,
        program_id=source.program_id,
        cohort_id=target.id,
        status=EnrollmentStatus.PENDING_APPROVAL,
        payment_status=PaymentStatus.PENDING,
        preferences=dict(source.preferences or {}),
        price_snapshot_amount=new_fee_kobo,
        currency_snapshot=programme.currency or "NGN",
        membership_policy_snapshot=_resolve_enrollment_membership_policy(
            programme, target
        ),
        uses_installments=bool(payload.transferable_credit_kobo),
    )
    db.add(new_enrollment)
    await db.flush()

    # Credit movement is represented by a new source-linked row, while old
    # credits are retained as transferred_out for a complete history.
    for previous in active_credits:
        previous.state = "transferred_out"
    if payload.transferable_credit_kobo:
        new_credit = AcademyFinancialCredit(
            enrollment_id=new_enrollment.id,
            source_enrollment_id=source.id,
            origin_credit_id=(active_credits[0].id if len(active_credits) == 1 else None),
            source_reference=f"cohort-change:{change.id}",
            source_kind="paid_transfer",
            amount_kobo=payload.transferable_credit_kobo,
            actor_auth_id=admin.user_id,
            state="active",
        )
        db.add(new_credit)
        await db.flush()
        installments = await _sync_installment_state_for_enrollment(
            db, new_enrollment, use_installments=True
        )
        payable = [
            item
            for item in installments
            if item.status not in {InstallmentStatus.PAID, InstallmentStatus.WAIVED}
        ]
        _, overshoot = apply_member_payment_across_installments(
            amount_kobo=payload.transferable_credit_kobo,
            installments=payable,
            now=now,
            payment_reference=new_credit.source_reference,
        )
        if overshoot:
            raise HTTPException(
                409, "Transfer credit cannot be applied to current tuition obligations"
            )
        await _sync_installment_state_for_enrollment(db, new_enrollment, now_dt=now)

    # Achievement claims / video references survive without being deleted
    # from the original cohort. Attendance sessions remain attached to the
    # cohort where they actually happened.
    for progress in source.progress_records:
        if not (
            progress.achieved_at or progress.reviewed_at or progress.evidence_media_id
        ):
            continue
        db.add(
            StudentProgress(
                enrollment_id=new_enrollment.id,
                milestone_id=progress.milestone_id,
                status=progress.status,
                achieved_at=progress.achieved_at,
                evidence_media_id=progress.evidence_media_id,
                score=progress.score,
                reviewed_by_coach_id=progress.reviewed_by_coach_id,
                reviewed_at=progress.reviewed_at,
                student_notes=progress.student_notes,
                coach_notes=(
                    f"Transferred from enrollment {source.id}. "
                    + (progress.coach_notes or "")
                ),
            )
        )

    if payload.refund_due_kobo:
        db.add(AcademyTransferRefundObligation(
            change_id=change.id,
            source_enrollment_id=source.id,
            member_auth_id=source.member_auth_id,
            amount_kobo=payload.refund_due_kobo,
            reason=payload.refund_reason or payload.reason,
            state="pending",
        ))

    change.state = "completed"
    change.to_enrollment_id = new_enrollment.id
    change.snapshot = {
        **(change.snapshot or {}),
        "approved_by_auth_id": admin.user_id,
        "approved_at": now.isoformat(),
        "approval_reason": payload.reason,
        "attendance_reviewed": payload.confirmed_attendance_review,
        "consumed_services_kobo": payload.consumed_services_kobo,
        "refund_due_kobo": payload.refund_due_kobo,
        "refund_reason": payload.refund_reason,
        "transferable_credit_kobo": payload.transferable_credit_kobo,
        "old_verified_tuition_kobo": verified_total,
        "credit_source_provenance": [
            {"id": str(credit.id), "reference": credit.source_reference, "amount_kobo": credit.amount_kobo}
            for credit in active_credits
        ],
        "original_tuition_kobo": source.price_snapshot_amount,
        "new_base_fee_kobo": base_fee_kobo,
        "new_discount_kobo": payload.discount_kobo,
        "new_discount_reason": payload.discount_reason,
        "new_tuition_kobo": new_fee_kobo,
        "payment_references_verified": financial.get("references") or [],
        "financial_resolution": "verified_paid_reassignment",
    }
    await db.commit()
    return {
        "state": "completed",
        "enrollment_id": str(new_enrollment.id),
        "new_tuition_kobo": new_fee_kobo,
        "transferred_credit_kobo": payload.transferable_credit_kobo,
        "remaining_tuition_kobo": new_fee_kobo - payload.transferable_credit_kobo,
        "refund_due_kobo": payload.refund_due_kobo,
        "idempotent": False,
    }
