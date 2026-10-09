"""Admin-controlled commercial amendment of an EXISTING Academy enrollment.

The payment owner verifies receipts. Academy edits only unpaid obligations and
records the original and revised terms. Historical paid installments are immutable.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from libs.common.datetime_utils import utc_now
from services.academy_service.models import (
    AcademyFinancialCredit,
    Enrollment,
    EnrollmentStatus,
    InstallmentStatus,
    PaymentStatus,
)
from services.academy_service.models.commercial_adjustment import AcademyCommercialAdjustment
from services.academy_service.routers._shared import _sync_installment_state_for_enrollment
from .change_cohort import financial_state

router = APIRouter()


class CommercialTermsRequest(BaseModel):
    adjustment_id: uuid.UUID
    reason: str = Field(min_length=20, max_length=2000)
    expected_price_snapshot_kobo: int = Field(ge=0)
    expected_installments: list[dict] = Field(min_length=1, max_length=6)
    unpaid_installment_amounts_kobo: list[int] = Field(min_length=1, max_length=6)
    historic_discount_kobo: int = Field(default=0, ge=0)
    agreed_cash_total_kobo: int = Field(gt=0)

    @model_validator(mode="after")
    def amounts_valid(self):
        if any(not isinstance(x, int) or isinstance(x, bool) or x <= 0 for x in self.unpaid_installment_amounts_kobo):
            raise ValueError("Unpaid amounts must be positive integer kobo")
        return self


async def _load(db: AsyncSession, enrollment_id: uuid.UUID, *, lock: bool = False):
    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(
            selectinload(Enrollment.installments),
            selectinload(Enrollment.program),
            selectinload(Enrollment.cohort),
        )
    )
    if lock:
        query = query.with_for_update()
    enrollment = (await db.execute(query)).scalar_one_or_none()
    if not enrollment:
        raise HTTPException(404, "Academy enrollment not found")
    if enrollment.status not in {EnrollmentStatus.PENDING_APPROVAL, EnrollmentStatus.ENROLLED}:
        raise HTTPException(409, "Only active Academy enrollments can receive commercial amendments")
    if not enrollment.cohort_id or not enrollment.member_auth_id or not enrollment.price_snapshot_amount:
        raise HTTPException(409, "Enrollment must have a cohort, owner and frozen tuition")
    return enrollment


def _snapshot(enrollment: Enrollment):
    return [
        {
            "id": str(item.id),
            "number": item.installment_number,
            "amount_kobo": item.amount,
            "status": item.status.value,
            "reference": item.payment_reference,
        }
        for item in sorted(enrollment.installments, key=lambda x: x.installment_number)
    ]


async def _verified(db: AsyncSession, enrollment: Enrollment):
    finance = await financial_state(enrollment.id)
    if not finance.get("paid_transfer_eligible", False):
        raise HTTPException(409, "Unresolved payment attempts or uploaded proofs must be reconciled first")
    credits = (
        (await db.execute(select(AcademyFinancialCredit).where(
            AcademyFinancialCredit.enrollment_id == enrollment.id,
            AcademyFinancialCredit.state == "active",
        ))).scalars().all()
    )
    # Cash and credited money, but never discount value.
    verified_cash = int(finance.get("verified_paid_tuition_kobo") or 0) + sum(
        credit.amount_kobo for credit in credits
    )
    references = set(finance.get("references") or [])
    for installment in enrollment.installments:
        if installment.status == InstallmentStatus.PAID and installment.payment_reference:
            if not installment.payment_reference.startswith((
                "academy-receipt-allocation:", "cohort-change:"
            )) and installment.payment_reference not in references:
                raise HTTPException(409, "Historical paid installment has unverified payment reference")
    if any(i.status == InstallmentStatus.WAIVED for i in enrollment.installments):
        raise HTTPException(409, "Waived installments require separate finance reconciliation")
    return verified_cash, finance


@router.get("/admin/enrollments/{enrollment_id}/commercial-terms")
async def preview_commercial_terms(
    enrollment_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    enrollment = await _load(db, enrollment_id)
    verified_cash, finance = await _verified(db, enrollment)
    installments = _snapshot(enrollment)
    if not installments:
        raise HTTPException(409, "Enrollment has no existing installment plan")
    return {
        "enrollment_id": str(enrollment.id),
        "price_snapshot_kobo": enrollment.price_snapshot_amount,
        "verified_cash_kobo": verified_cash,
        "installments": installments,
        "blocked_payment_references": finance.get("blocked_references", []),
        "adjustments": [
            {
                "id": str(item.id),
                "reason": item.reason,
                "actor_auth_id": item.actor_auth_id,
                "created_at": item.created_at.isoformat(),
                "approved_terms": item.approved_terms,
            }
            for item in (await db.execute(
                select(AcademyCommercialAdjustment)
                .where(AcademyCommercialAdjustment.enrollment_id == enrollment.id)
                .order_by(AcademyCommercialAdjustment.created_at.desc())
            )).scalars().all()
        ],
    }


@router.post("/admin/enrollments/{enrollment_id}/commercial-terms")
async def approve_commercial_terms(
    enrollment_id: uuid.UUID,
    payload: CommercialTermsRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    prior = await db.get(AcademyCommercialAdjustment, payload.adjustment_id)
    if prior:
        if prior.enrollment_id != enrollment_id:
            raise HTTPException(409, "Adjustment ID belongs to a different enrollment")
        if prior.approved_terms.get("request") != payload.model_dump(mode="json"):
            raise HTTPException(409, "Adjustment ID already used with different terms")
        return {"state": "completed", "idempotent": True, "adjustment_id": str(prior.id)}
    enrollment = await _load(db, enrollment_id, lock=True)
    if enrollment.price_snapshot_amount != payload.expected_price_snapshot_kobo:
        raise HTTPException(409, "Tuition has changed; reload the review")
    old = _snapshot(enrollment)
    if old != payload.expected_installments:
        raise HTTPException(409, "Installments have changed; reload the review")
    if not old:
        raise HTTPException(409, "Cannot amend a schedule that does not exist")
    verified_cash, finance = await _verified(db, enrollment)
    paid = [i for i in enrollment.installments if i.status == InstallmentStatus.PAID]
    pending = [i for i in enrollment.installments if i.status in {InstallmentStatus.PENDING, InstallmentStatus.MISSED}]
    if not paid or not pending or len(pending) != len(payload.unpaid_installment_amounts_kobo):
        raise HTTPException(409, "Expected paid history and unpaid installment count do not match")
    if any(i.payment_reference for i in pending):
        raise HTTPException(409, "Unpaid installments with a checkout reference must be reconciled and closed first")
    nominal_paid = sum(i.amount for i in paid)
    if payload.historic_discount_kobo > nominal_paid:
        raise HTTPException(409, "Historical discount exceeds settled obligation")
    if nominal_paid - payload.historic_discount_kobo != verified_cash:
        raise HTTPException(409, "Historical discount and verified cash do not reconcile")
    new_nominal = nominal_paid + sum(payload.unpaid_installment_amounts_kobo)
    if new_nominal - payload.historic_discount_kobo != payload.agreed_cash_total_kobo:
        raise HTTPException(409, "Negotiated cash total does not match historical discount and future schedule")
    if new_nominal > enrollment.price_snapshot_amount:
        raise HTTPException(409, "This adjustment cannot increase the frozen tuition")
    if enrollment.payment_status == PaymentStatus.WAIVED:
        raise HTTPException(409, "Waived enrollment requires a separate financial review")
    for item, amount in zip(sorted(pending, key=lambda x: x.installment_number), payload.unpaid_installment_amounts_kobo):
        item.amount = amount
    enrollment.price_snapshot_amount = new_nominal
    enrollment.uses_installments = True
    db.add(AcademyCommercialAdjustment(
        id=payload.adjustment_id,
        enrollment_id=enrollment.id,
        actor_auth_id=admin.user_id,
        reason=payload.reason,
        original_terms={
            "price_snapshot_kobo": payload.expected_price_snapshot_kobo,
            "installments": old,
            "verified_cash_kobo": verified_cash,
            "verified_payment_references": finance.get("references") or [],
        },
        approved_terms={
            "request": payload.model_dump(mode="json"),
            "nominal_tuition_kobo": new_nominal,
            "historic_discount_kobo": payload.historic_discount_kobo,
            "agreed_cash_total_kobo": payload.agreed_cash_total_kobo,
        },
    ))
    await _sync_installment_state_for_enrollment(db, enrollment, now_dt=utc_now())
    await db.commit()
    return {
        "state": "completed",
        "idempotent": False,
        "adjustment_id": str(payload.adjustment_id),
        "nominal_tuition_kobo": new_nominal,
        "agreed_cash_total_kobo": payload.agreed_cash_total_kobo,
        "remaining_cash_kobo": sum(payload.unpaid_installment_amounts_kobo),
    }
