"""Verified bank receipt split ledger. Allocations are NOT additional income."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.service_client import internal_get, internal_post
from libs.common.config import get_settings
from libs.db.session import get_async_db
from libs.common.datetime_utils import utc_now
from services.payments_service.services.manual_transfer import lock_external_reference
from services.payments_service.services.ledger_emit import emit_payment_to_ledger
from services.payments_service.models import (
    AcademyBankReceipt,
    AcademyReceiptAllocation,
    Payment,
    PaymentStatus,
)

router = APIRouter(prefix="/admin/academy-receipts", tags=["academy-receipts"])


class VerifyReceiptRequest(BaseModel):
    external_reference: str = Field(min_length=5, max_length=160)
    amount_kobo: int = Field(gt=0)
    verification_note: str = Field(min_length=20, max_length=2000)


class AllocateReceiptRequest(BaseModel):
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
    # One canonical payment records cash-in, while allocations only track
    # beneficiaries and never book any additional income.
    await lock_external_reference(db, body.external_reference)
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
    now = utc_now()
    canonical = Payment(
        reference=f"ACADEMY-RECEIPT-{uuid.uuid4()}",
        member_auth_id=admin.user_id,
        purpose="academy_cohort",
        amount=body.amount_kobo / 100,
        currency="NGN",
        status=PaymentStatus.PAID,
        provider="offline",
        provider_reference=reference,
        payment_method="bank_transfer",
        paid_at=now,
        entitlement_applied_at=now,  # Master receipt never activates an enrollment.
        payment_metadata={
            "academy_shared_bank_receipt_master": True,
            "verified_by_auth_id": admin.user_id,
            "verification_note": body.verification_note,
        },
    )
    db.add(canonical)
    await db.flush()
    receipt = AcademyBankReceipt(
        payment_id=canonical.id,
        external_reference=reference,
        amount_kobo=body.amount_kobo,
        currency="NGN",
        verification_note=body.verification_note,
        verified_by_auth_id=admin.user_id,
    )
    db.add(receipt)
    await db.flush()
    await db.commit()
    # The existing ledger posts exactly one cash-in under canonical.reference.
    # Allocation writes do not emit journal entries.
    await emit_payment_to_ledger(db, canonical)
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
    beneficiary_auth_id = str(enrollment.get("member_auth_id") or "")
    if not beneficiary_auth_id:
        raise HTTPException(409, "Academy enrollment has no verified member identity")
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
        member_auth_id=beneficiary_auth_id,
        enrollment_id=body.enrollment_id,
        amount_kobo=body.amount_kobo,
        idempotency_key=body.idempotency_key,
        state="reserved",
        created_by_auth_id=admin.user_id,
    ))
    await db.flush()
    await db.commit()
    return await _receipt_summary(db, receipt)


@router.post("/{receipt_id}/allocations/{allocation_id}/apply")
async def apply_receipt_allocation(
    receipt_id: uuid.UUID,
    allocation_id: uuid.UUID,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Deliver verified credit to its true enrollment, with retry-safe acknowledgement.

    Payments records cash once at receipt verification. The Academy service
    applies tuition credit and owns installments; no synthetic second payment.
    """
    allocation = (await db.execute(
        select(AcademyReceiptAllocation).where(
            AcademyReceiptAllocation.id == allocation_id,
            AcademyReceiptAllocation.receipt_id == receipt_id,
        ).with_for_update()
    )).scalar_one_or_none()
    if not allocation:
        raise HTTPException(404, "Receipt allocation not found")
    if allocation.state == "applied":
        return {"state": "applied", "allocation_id": str(allocation.id), "idempotent": True}
    if allocation.state != "reserved":
        raise HTTPException(409, "Allocation is not eligible for application")
    source_reference = f"academy-receipt-allocation:{allocation.id}"
    try:
        response = await internal_post(
            service_url=get_settings().ACADEMY_SERVICE_URL,
            path=f"/internal/academy/enrollments/{allocation.enrollment_id}/verified-credit",
            calling_service="payments",
            json={
                "source_reference": source_reference,
                "source_kind": "receipt_allocation",
                "member_auth_id": allocation.member_auth_id,
                "amount_kobo": allocation.amount_kobo,
                "actor_auth_id": admin.user_id,
            },
        )
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(
            503, "Academy credit could not be confirmed. Do not re-record the bank receipt; retry this allocation"
        ) from exc
    allocation.state = "applied"
    allocation.applied_at = utc_now()
    await db.commit()
    return {
        "state": "applied", "allocation_id": str(allocation.id),
        "enrollment_id": str(allocation.enrollment_id), "idempotent": False,
    }
