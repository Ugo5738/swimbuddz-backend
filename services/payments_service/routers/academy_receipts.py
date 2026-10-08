"""Verified bank receipt split ledger. Allocations are NOT additional income."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.service_client import internal_get
from libs.common.config import get_settings
from libs.db.session import get_async_db
from services.payments_service.models import (
    AcademyBankReceipt,
    AcademyReceiptAllocation,
    Payment,
    PaymentStatus,
)

router = APIRouter(prefix="/payments/admin/academy-receipts", tags=["academy-receipts"])


class VerifyReceiptRequest(BaseModel):
    external_reference: str = Field(min_length=5, max_length=160)
    amount_kobo: int = Field(gt=0)
    verification_note: str = Field(min_length=20, max_length=2000)


class AllocateReceiptRequest(BaseModel):
    member_auth_id: str = Field(min_length=1)
    enrollment_id: uuid.UUID
    amount_kobo: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8, max_length=160)


async def _receipt_summary(db: AsyncSession, receipt: AcademyBankReceipt) -> dict:
    allocations = (await db.execute(
        select(AcademyReceiptAllocation)
        .where(AcademyReceiptAllocation.receipt_id == receipt.id)
        .order_by(AcademyReceiptAllocation.created_at)
    )).scalars().all()
    assigned = sum(a.amount_kobo for a in allocations if a.state != "void")
    return {
        "id": str(receipt.id),
        "external_reference": receipt.external_reference,
        "amount_kobo": receipt.amount_kobo,
        "currency": receipt.currency,
        "verification_note": receipt.verification_note,
        "allocated_kobo": assigned,
        "unallocated_kobo": receipt.amount_kobo - assigned,
        "allocations": [
            {"id": str(a.id), "enrollment_id": str(a.enrollment_id),
             "member_auth_id": a.member_auth_id, "amount_kobo": a.amount_kobo,
             "state": a.state, "idempotency_key": a.idempotency_key}
            for a in allocations
        ],
    }


@router.post("")
async def verify_bank_receipt(
    body: VerifyReceiptRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    # This is attribution of verified funds, not a payment or new cash-in.
    # Existing paid Payment rows may already represent the same cash; never
    # silently create a second receipt with the same bank reference.
    reference = body.external_reference.strip().upper()
    existing = (await db.execute(
        select(AcademyBankReceipt)
        .where(AcademyBankReceipt.external_reference == reference)
        .with_for_update()
    )).scalar_one_or_none()
    if existing:
        if existing.amount_kobo != body.amount_kobo:
            raise HTTPException(409, "A different amount is already verified under this bank reference")
        return await _receipt_summary(db, existing)
    payment = (await db.execute(
        select(Payment.id).where(
            Payment.status == PaymentStatus.PAID,
            func.upper(Payment.provider_reference) == reference,
        ).limit(1)
    )).scalar_one_or_none()
    if payment:
        raise HTTPException(
            409, "An existing payment already settled this bank reference. Reconcile that payment rather than record a second receipt"
        )
    receipt = AcademyBankReceipt(
        external_reference=reference,
        amount_kobo=body.amount_kobo,
        currency="NGN",
        verification_note=body.verification_note,
        verified_by_auth_id=admin.user_id,
    )
    db.add(receipt)
    await db.flush()
    await db.commit()
    return await _receipt_summary(db, receipt)


@router.get("/{receipt_id}")
async def get_verified_receipt(
    receipt_id: uuid.UUID,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    receipt = (await db.execute(
        select(AcademyBankReceipt).where(AcademyBankReceipt.id == receipt_id)
    )).scalar_one_or_none()
    if not receipt:
        raise HTTPException(404, "Verified receipt not found")
    return await _receipt_summary(db, receipt)


@router.post("/{receipt_id}/allocations")
async def allocate_receipt(
    receipt_id: uuid.UUID,
    body: AllocateReceiptRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    receipt = (await db.execute(
        select(AcademyBankReceipt)
        .where(AcademyBankReceipt.id == receipt_id)
        .with_for_update()
    )).scalar_one_or_none()
    if not receipt:
        raise HTTPException(404, "Verified receipt not found")
    prior = (await db.execute(
        select(AcademyReceiptAllocation).where(
            AcademyReceiptAllocation.receipt_id == receipt_id,
            AcademyReceiptAllocation.idempotency_key == body.idempotency_key,
        )
    )).scalar_one_or_none()
    if prior:
        if (
            prior.amount_kobo != body.amount_kobo
            or prior.member_auth_id != body.member_auth_id
            or prior.enrollment_id != body.enrollment_id
        ):
            raise HTTPException(409, "Idempotency key belongs to a different allocation")
        return await _receipt_summary(db, receipt)

    # Verify actual Academy enrollment ownership from the source service.
    # A failed lookup always blocks allocation, not creates orphan credit.
    try:
        response = await internal_get(
            service_url=get_settings().ACADEMY_SERVICE_URL,
            path=f"/internal/academy/enrollments/{body.enrollment_id}",
            calling_service="payments",
        )
        response.raise_for_status()
        enrollment = response.json()
    except Exception as exc:
        raise HTTPException(503, "Academy enrollment ownership cannot be verified") from exc
    if str(enrollment.get("member_auth_id")) != body.member_auth_id:
        raise HTTPException(403, "The target enrollment belongs to another member")
    if str(enrollment.get("currency_snapshot") or "NGN").upper() != receipt.currency:
        raise HTTPException(409, "Currency mismatch")

    assigned = (await db.execute(
        select(func.coalesce(func.sum(AcademyReceiptAllocation.amount_kobo), 0))
        .where(AcademyReceiptAllocation.receipt_id == receipt_id,
               AcademyReceiptAllocation.state != "void")
    )).scalar_one()
    if assigned + body.amount_kobo > receipt.amount_kobo:
        raise HTTPException(409, "Allocations exceed the verified bank receipt")

    db.add(AcademyReceiptAllocation(
        receipt_id=receipt_id,
        member_auth_id=body.member_auth_id,
        enrollment_id=body.enrollment_id,
        amount_kobo=body.amount_kobo,
        idempotency_key=body.idempotency_key,
        state="reserved",
        created_by_auth_id=admin.user_id,
    ))
    await db.flush()
    await db.commit()
    return await _receipt_summary(db, receipt)
