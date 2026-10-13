"""Guarded deletion of erroneous, financially empty Academy enrollments."""

import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.academy_service.models import (
    AcademyEnrollmentChange,
    AcademyEnrollmentDeletionAudit,
    AcademyFinancialCredit,
    AcademyTransferRefundObligation,
    Enrollment,
    EnrollmentStatus,
    InstallmentStatus,
    PaymentStatus,
    MilestoneEvidence,
    StudentProgress,
)
from services.academy_service.models.commercial_adjustment import (
    AcademyCommercialAdjustment,
)
from .change_cohort import financial_state

router = APIRouter()


class ConfirmDelete(BaseModel):
    preview_token: str = Field(min_length=64, max_length=64)
    confirmation: str
    reason: str = Field(min_length=20, max_length=1000)


async def inspect(db: AsyncSession, enrollment: Enrollment):
    eid = enrollment.id
    checks = {
        "cohort_transfers": await db.scalar(
            select(func.count())
            .select_from(AcademyEnrollmentChange)
            .where(
                (AcademyEnrollmentChange.from_enrollment_id == eid)
                | (AcademyEnrollmentChange.to_enrollment_id == eid)
            )
        ),
        "financial_credits": await db.scalar(
            select(func.count())
            .select_from(AcademyFinancialCredit)
            .where(
                (AcademyFinancialCredit.enrollment_id == eid)
                | (AcademyFinancialCredit.source_enrollment_id == eid)
            )
        ),
        "refund_obligations": await db.scalar(
            select(func.count())
            .select_from(AcademyTransferRefundObligation)
            .where(AcademyTransferRefundObligation.source_enrollment_id == eid)
        ),
        "commercial_adjustments": await db.scalar(
            select(func.count())
            .select_from(AcademyCommercialAdjustment)
            .where(AcademyCommercialAdjustment.enrollment_id == eid)
        ),
        "progress": await db.scalar(
            select(func.count())
            .select_from(StudentProgress)
            .where(StudentProgress.enrollment_id == eid)
        ),
        "milestone_evidence": await db.scalar(
            select(func.count())
            .select_from(MilestoneEvidence)
            .where(MilestoneEvidence.enrollment_id == eid)
        ),
    }
    blockers = [name for name, count in checks.items() if count]
    if enrollment.status not in {
        EnrollmentStatus.PENDING_APPROVAL,
        EnrollmentStatus.WAITLIST,
        EnrollmentStatus.DROPPED,
    }:
        blockers.append("active_or_historical_status")
    if (
        enrollment.payment_reference
        or enrollment.paid_at
        or enrollment.certificate_issued_at
        or enrollment.certificate_code
    ):
        blockers.append("payment_or_certificate_history")
    if enrollment.enrolled_at or enrollment.dropped_at or enrollment.paused_at:
        blockers.append("enrollment_activity")
    if enrollment.paid_installments_count or enrollment.missed_installments_count:
        blockers.append("installment_activity")
    if any(
        i.status != InstallmentStatus.PENDING or i.payment_reference or i.paid_at
        for i in enrollment.installments
    ):
        blockers.append("settled_or_modified_installments")
    # Never use Academy-only local state to assume absence of money.
    financial = await financial_state(eid)
    attempts = financial.get("attempts") or []
    # A fully documented provider/bank closure is not a tuition receipt.
    # Preserve failed Payment attempts in Payments and an audit snapshot here;
    # do not delete those attempts or change their financial history.
    closed_unpaid = (
        bool(attempts)
        and bool(financial.get("all_unpaid_closed"))
        and all(
            item.get("status") == "failed"
            and item.get("closed_unpaid")
            and not item.get("proof_submitted")
            and not item.get("entitlement_applied")
            for item in attempts
        )
    )
    if (
        financial.get("has_payment_activity") or financial.get("references") or attempts
    ) and not closed_unpaid:
        blockers.append("unclosed_payment_attempts")
    if financial.get("verified_paid_tuition_kobo") or financial.get(
        "blocked_references"
    ):
        blockers.append("financial_history_or_unresolved_checkout")
    return checks, sorted(set(blockers)), financial


def snapshot(enrollment, checks, blockers, financial):
    return {
        "enrollment_id": str(enrollment.id),
        "member_id": str(enrollment.member_id),
        "member_auth_id": enrollment.member_auth_id,
        "cohort_id": str(enrollment.cohort_id) if enrollment.cohort_id else None,
        "program_id": str(enrollment.program_id) if enrollment.program_id else None,
        "status": enrollment.status.value,
        "payment_status": enrollment.payment_status.value,
        "installment_count": len(enrollment.installments),
        "dependency_counts": checks,
        "blockers": blockers,
        "payment_attempt_count": len(financial.get("attempts") or []),
        "payment_attempts_preserved": financial.get("attempts") or [],
        "created_at": enrollment.created_at.isoformat()
        if enrollment.created_at
        else None,
        "updated_at": enrollment.updated_at.isoformat()
        if enrollment.updated_at
        else None,
    }


async def load(db, eid, lock=False):
    query = (
        select(Enrollment)
        .where(Enrollment.id == eid)
        .options(selectinload(Enrollment.installments))
    )
    if lock:
        query = query.with_for_update()
    return (await db.execute(query)).scalar_one_or_none()


@router.get("/admin/enrollments/{enrollment_id}/deletion-preview")
async def deletion_preview(
    enrollment_id: uuid.UUID,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    enrollment = await load(db, enrollment_id)
    if not enrollment:
        raise HTTPException(404, "Enrollment not found")
    checks, blockers, financial = await inspect(db, enrollment)
    data = snapshot(enrollment, checks, blockers, financial)
    token = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return {
        "eligible": not blockers,
        "blockers": blockers,
        "snapshot": data,
        "preview_token": token,
        "requires_confirmation": str(enrollment_id),
    }


@router.post("/admin/enrollments/{enrollment_id}/delete-empty")
async def delete_empty_enrollment(
    enrollment_id: uuid.UUID,
    body: ConfirmDelete,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    enrollment = await load(db, enrollment_id, lock=True)
    if not enrollment:
        raise HTTPException(404, "Enrollment not found")
    if body.confirmation != str(enrollment_id):
        raise HTTPException(422, "Type the exact enrollment ID to confirm deletion")
    checks, blockers, financial = await inspect(db, enrollment)
    data = snapshot(enrollment, checks, blockers, financial)
    token = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    if token != body.preview_token:
        raise HTTPException(409, "Enrollment changed; refresh the deletion preview")
    if blockers:
        raise HTTPException(
            409,
            {
                "message": "Deletion is blocked by protected history",
                "blockers": blockers,
            },
        )
    db.add(
        AcademyEnrollmentDeletionAudit(
            enrollment_id=enrollment.id,
            member_id=enrollment.member_id,
            member_auth_id=enrollment.member_auth_id,
            actor_auth_id=admin.user_id,
            reason=body.reason,
            snapshot=data,
        )
    )
    await db.delete(enrollment)
    await db.commit()
    return {
        "deleted": True,
        "enrollment_id": str(enrollment_id),
        "audit_preserved": True,
    }
