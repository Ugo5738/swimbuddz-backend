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
from libs.common.currency import naira_to_kobo
from services.payments_service.services.manual_transfer import lock_external_reference
from services.payments_service.services.ledger_emit import emit_payment_to_ledger
from services.payments_service.models import (
    AcademyBankReceipt,
    AcademyReceiptAllocation,
    Payment,
    PaymentPurpose,
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
    allocations = (
        (
            await db.execute(
                select(AcademyReceiptAllocation)
                .where(AcademyReceiptAllocation.receipt_id == receipt.id)
                .order_by(AcademyReceiptAllocation.created_at)
            )
        )
        .scalars()
        .all()
    )
    assigned = sum(a.amount_kobo for a in allocations if a.state != "void")
    return {
        "id": str(receipt.id),
        "external_reference": receipt.external_reference,
        "amount_kobo": receipt.amount_kobo,
        "preexisting_paid_kobo": receipt.preexisting_paid_kobo or 0,
        "new_cash_kobo": receipt.amount_kobo - (receipt.preexisting_paid_kobo or 0),
        "currency": receipt.currency,
        "verification_note": receipt.verification_note,
        "allocated_kobo": assigned,
        "unallocated_kobo": receipt.amount_kobo - assigned,
        "allocations": [
            {
                "id": str(a.id),
                "enrollment_id": str(a.enrollment_id),
                "member_auth_id": a.member_auth_id,
                "amount_kobo": a.amount_kobo,
                "state": a.state,
                "idempotency_key": a.idempotency_key,
            }
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
    reference = body.external_reference.strip().upper()
    await lock_external_reference(db, reference)
    # Existing paid Payment rows may already represent the same cash; never
    # silently create a second receipt with the same bank reference.
    existing = (
        await db.execute(
            select(AcademyBankReceipt)
            .where(AcademyBankReceipt.external_reference == reference)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing:
        if existing.amount_kobo != body.amount_kobo:
            raise HTTPException(
                409, "A different amount is already verified under this bank reference"
            )
        return await _receipt_summary(db, existing)
    payment = (
        await db.execute(
            select(Payment.id)
            .where(
                Payment.status == PaymentStatus.PAID,
                func.upper(Payment.provider_reference) == reference,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if payment:
        raise HTTPException(
            409,
            "An existing payment already settled this bank reference. Reconcile that payment rather than record a second receipt",
        )
    now = utc_now()
    canonical = Payment(
        reference=f"ACADEMY-RECEIPT-{uuid.uuid4()}",
        member_auth_id="shared-academy-bank-receipt",
        purpose=PaymentPurpose.ACADEMY_COHORT,
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


class AdoptSettledAcademyReceiptRequest(BaseModel):
    original_payment_reference: str = Field(min_length=4, max_length=180)
    external_reference: str = Field(min_length=5, max_length=160)
    actual_bank_amount_kobo: int = Field(gt=0)
    reviewed_bank_evidence: str = Field(min_length=30, max_length=2000)
    confirm_original_payment_is_one_beneficiary: bool
    confirm_unrecorded_remainder: bool


@router.post("/adopt-settled")
async def adopt_previously_settled_academy_receipt(
    body: AdoptSettledAcademyReceiptRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Adopt a settled single-student payment, creating cash-in ONLY for a
    separately confirmed, previously unrecorded bank remainder.

    Paid history and previous ledger postings are never edited. This is
    deliberately not a refund, reversal or entitlement reapplication.
    """
    reference = body.external_reference.strip().upper()
    await lock_external_reference(db, reference)
    payment = (
        await db.execute(
            select(Payment)
            .where(Payment.reference == body.original_payment_reference)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not payment or payment.status != PaymentStatus.PAID:
        raise HTTPException(409, "Original payment must exist and be settled")
    if payment.purpose != PaymentPurpose.ACADEMY_COHORT:
        raise HTTPException(409, "Only settled Academy tuition can be adopted")
    if payment.currency != "NGN" or not payment.paid_at or not payment.entitlement_applied_at:
        raise HTTPException(409, "Original payment is not fully settled in NGN")
    if payment.provider not in {"offline", "manual_transfer"}:
        raise HTTPException(409, "Only verified offline bank payments qualify")
    meta = payment.payment_metadata or {}
    enrollment_id = meta.get("enrollment_id")
    if not enrollment_id or not payment.member_auth_id:
        raise HTTPException(409, "Original Academy enrollment identity is missing")
    if not body.confirm_original_payment_is_one_beneficiary or not body.confirm_unrecorded_remainder:
        raise HTTPException(422, "Explicit bank and beneficiary reconciliation confirmations required")
    paid_kobo = naira_to_kobo(payment.amount)
    if paid_kobo <= 0 or body.actual_bank_amount_kobo <= paid_kobo:
        raise HTTPException(409, "Bank amount must exceed original paid Academy allocation")
    if body.actual_bank_amount_kobo > 100 * paid_kobo:
        raise HTTPException(409, "Suspicious receipt total; investigate outside this workflow")
    # Do not import/operate Academy ORM: validate through its existing ownership endpoint.
    try:
        identity = await internal_get(
            service_url=get_settings().ACADEMY_SERVICE_URL,
            path=f"/internal/academy/enrollments/{enrollment_id}/allocation-identity",
            calling_service="payments",
        )
        identity.raise_for_status()
        owner = identity.json()
    except Exception as exc:
        raise HTTPException(503, "Cannot independently verify original learner") from exc
    if str(owner.get("member_auth_id")) != payment.member_auth_id:
        raise HTTPException(409, "Original paid enrollment no longer matches payer")
    if str(owner.get("currency_snapshot") or "NGN").upper() != "NGN":
        raise HTTPException(409, "Original enrollment currency mismatch")

    existing = (
        await db.execute(
            select(AcademyBankReceipt).where(
                (AcademyBankReceipt.payment_id == payment.id)
                | (AcademyBankReceipt.external_reference == reference)
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if existing:
        if (existing.payment_id == payment.id
            and existing.external_reference == reference
            and existing.amount_kobo == body.actual_bank_amount_kobo):
            return await _receipt_summary(db, existing)
        raise HTTPException(409, "Existing receipt was adopted with different terms")

    # The original bank reference can be a placeholder in older proof uploads.
    # Reject any OTHER paid payment for this bank reference and require human
    # verification of the external bank statement against actual cash-in.
    duplicates = (
        (await db.execute(
            select(Payment).where(
                Payment.status == PaymentStatus.PAID,
                func.upper(Payment.provider_reference) == reference,
                Payment.id != payment.id,
            )
        )).scalars().all()
    )
    if duplicates:
        raise HTTPException(409, "Another PAID payment already uses this bank reference")
    remainder_kobo = body.actual_bank_amount_kobo - paid_kobo
    now = utc_now()
    # A new Payment records ONLY the unrecorded cash. Neither the original
    # payment nor the original entitlement is changed.
    remainder = Payment(
        reference=f"ACADEMY-REMAINDER-{uuid.uuid4()}",
        member_auth_id="shared-academy-bank-receipt",
        purpose=PaymentPurpose.ACADEMY_COHORT,
        amount=remainder_kobo / 100,
        currency="NGN",
        status=PaymentStatus.PAID,
        provider="offline",
        # Keep canonical bank reference on the original payment. The derived
        # remainder is linked to the receipt and cannot be settled independently.
        payment_method="bank_transfer",
        paid_at=now,
        entitlement_applied_at=now,
        payment_metadata={
            "academy_shared_bank_receipt_remainder": True,
            "original_payment_reference": payment.reference,
            "bank_reference": reference,
            "verified_by_auth_id": admin.user_id,
            "verified_bank_evidence": body.reviewed_bank_evidence,
        },
    )
    db.add(remainder)
    await db.flush()
    receipt = AcademyBankReceipt(
        payment_id=payment.id,
        remainder_payment_id=remainder.id,
        preexisting_paid_kobo=paid_kobo,
        external_reference=reference,
        amount_kobo=body.actual_bank_amount_kobo,
        currency="NGN",
        verification_note=body.reviewed_bank_evidence,
        verified_by_auth_id=admin.user_id,
    )
    db.add(receipt)
    await db.flush()
    # Historical allocation is deliberately NOT a new tuition credit.
    db.add(AcademyReceiptAllocation(
        receipt_id=receipt.id,
        member_auth_id=payment.member_auth_id,
        enrollment_id=uuid.UUID(str(enrollment_id)),
        amount_kobo=paid_kobo,
        idempotency_key=f"historical-payment:{payment.id}",
        state="historical",
        created_by_auth_id=admin.user_id,
        applied_at=payment.entitlement_applied_at,
    ))
    await db.commit()
    # Ledger idempotency is keyed to the remainder payment, not the old cash-in.
    await emit_payment_to_ledger(db, remainder)
    return await _receipt_summary(db, receipt)


@router.get("/{receipt_id}")
async def get_verified_receipt(
    receipt_id: uuid.UUID,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    receipt = (
        await db.execute(
            select(AcademyBankReceipt).where(AcademyBankReceipt.id == receipt_id)
        )
    ).scalar_one_or_none()
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
    receipt = (
        await db.execute(
            select(AcademyBankReceipt)
            .where(AcademyBankReceipt.id == receipt_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not receipt:
        raise HTTPException(404, "Verified receipt not found")
    prior = (
        await db.execute(
            select(AcademyReceiptAllocation).where(
                AcademyReceiptAllocation.receipt_id == receipt_id,
                AcademyReceiptAllocation.idempotency_key == body.idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if prior:
        if prior.state == "void":
            raise HTTPException(
                409, "This reservation was voided. Start a new reviewed allocation."
            )
        if (
            prior.amount_kobo != body.amount_kobo
            or prior.enrollment_id != body.enrollment_id
        ):
            raise HTTPException(
                409, "Idempotency key belongs to a different allocation"
            )
        return await _receipt_summary(db, receipt)

    # Verify actual Academy enrollment ownership from the source service.
    # A failed lookup always blocks allocation, not creates orphan credit.
    try:
        response = await internal_get(
            service_url=get_settings().ACADEMY_SERVICE_URL,
            path=f"/internal/academy/enrollments/{body.enrollment_id}/allocation-identity",
            calling_service="payments",
        )
        response.raise_for_status()
        enrollment = response.json()
    except Exception as exc:
        raise HTTPException(
            503, "Academy enrollment ownership cannot be verified"
        ) from exc
    beneficiary_auth_id = str(enrollment.get("member_auth_id") or "")
    if not beneficiary_auth_id:
        raise HTTPException(409, "Academy enrollment has no verified member identity")
    if str(enrollment.get("currency_snapshot") or "NGN").upper() != receipt.currency:
        raise HTTPException(409, "Currency mismatch")

    existing_beneficiary = (
        await db.execute(
            select(AcademyReceiptAllocation).where(
                AcademyReceiptAllocation.receipt_id == receipt_id,
                AcademyReceiptAllocation.enrollment_id == body.enrollment_id,
                AcademyReceiptAllocation.state != "void",
            )
        )
    ).scalar_one_or_none()
    if existing_beneficiary:
        raise HTTPException(
            409,
            "An active allocation already exists for this learner and receipt. Review it before allocating again.",
        )

    assigned = (
        await db.execute(
            select(
                func.coalesce(func.sum(AcademyReceiptAllocation.amount_kobo), 0)
            ).where(
                AcademyReceiptAllocation.receipt_id == receipt_id,
                AcademyReceiptAllocation.state != "void",
            )
        )
    ).scalar_one()
    if assigned + body.amount_kobo > receipt.amount_kobo:
        raise HTTPException(409, "Allocations exceed the verified bank receipt")

    db.add(
        AcademyReceiptAllocation(
            receipt_id=receipt_id,
            member_auth_id=beneficiary_auth_id,
            enrollment_id=body.enrollment_id,
            amount_kobo=body.amount_kobo,
            idempotency_key=body.idempotency_key,
            state="reserved",
            created_by_auth_id=admin.user_id,
        )
    )
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
    allocation = (
        await db.execute(
            select(AcademyReceiptAllocation)
            .where(
                AcademyReceiptAllocation.id == allocation_id,
                AcademyReceiptAllocation.receipt_id == receipt_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not allocation:
        raise HTTPException(404, "Receipt allocation not found")
    if allocation.state == "applied":
        return {
            "state": "applied",
            "allocation_id": str(allocation.id),
            "idempotent": True,
        }
    if allocation.state != "reserved":
        raise HTTPException(409, "Historical or void allocations cannot be applied again")
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
            503,
            "Academy credit could not be confirmed. Do not re-record the bank receipt; retry this allocation",
        ) from exc
    allocation.state = "applied"
    allocation.applied_at = utc_now()
    await db.commit()
    return {
        "state": "applied",
        "allocation_id": str(allocation.id),
        "enrollment_id": str(allocation.enrollment_id),
        "idempotent": False,
    }


class ReconcileLegacyAttempt(BaseModel):
    payment_reference: str = Field(min_length=3, max_length=180)
    review_note: str = Field(min_length=20, max_length=2000)


@router.post("/{receipt_id}/reconcile-attempt")
async def link_superseded_academy_checkout_to_shared_receipt(
    receipt_id: uuid.UUID,
    body: ReconcileLegacyAttempt,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Retire an individual proof/checkout after its verified shared allocation.

    A proof is never treated as unpaid. Instead, we preserve it on the old
    attempt and link its claim to the one bank receipt already recorded.
    """
    receipt = (
        await db.execute(
            select(AcademyBankReceipt)
            .where(
                AcademyBankReceipt.id == receipt_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not receipt:
        raise HTTPException(404, "Shared bank receipt not found")
    payment = (
        await db.execute(
            select(Payment)
            .where(
                Payment.reference == body.payment_reference,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not payment or payment.purpose != PaymentPurpose.ACADEMY_COHORT:
        raise HTTPException(404, "Academy payment attempt not found")
    if payment.id == receipt.payment_id:
        raise HTTPException(
            409, "Cannot reconcile the canonical shared receipt against itself"
        )
    metadata = payment.payment_metadata or {}
    already_linked = metadata.get("superseded_by_shared_receipt") or {}
    if already_linked:
        if str(already_linked.get("receipt_id")) == str(receipt_id):
            return {
                "state": "reconciled",
                "payment_reference": payment.reference,
                "idempotent": True,
            }
        raise HTTPException(
            409, "This checkout already belongs to another verified receipt"
        )
    if (
        payment.status
        not in {
            PaymentStatus.PENDING,
            PaymentStatus.PENDING_REVIEW,
            PaymentStatus.FAILED,
        }
        or payment.entitlement_applied_at
    ):
        raise HTTPException(
            409,
            "Paid or fulfilled checkouts must be reconciled as paid funds, not superseded",
        )
    enrollment_id = metadata.get("enrollment_id")
    if not enrollment_id:
        raise HTTPException(409, "Original Academy enrollment is missing")
    allocations = (
        (
            await db.execute(
                select(AcademyReceiptAllocation).where(
                    AcademyReceiptAllocation.receipt_id == receipt_id,
                    AcademyReceiptAllocation.enrollment_id
                    == uuid.UUID(str(enrollment_id)),
                    AcademyReceiptAllocation.state == "applied",
                )
            )
        )
        .scalars()
        .all()
    )
    if not allocations:
        raise HTTPException(
            409, "Apply a verified receipt allocation to this learner first"
        )
    submitted = metadata.get("submitted_transfer") or {}
    proof_reference = (
        str(
            submitted.get("external_reference")
            or submitted.get("transaction_reference")
            or ""
        )
        .strip()
        .upper()
    )
    if proof_reference and proof_reference != receipt.external_reference:
        raise HTTPException(
            409, "Submitted proof references a different bank transaction"
        )
    payment.payment_metadata = {
        **metadata,
        "superseded_by_shared_receipt": {
            "receipt_id": str(receipt.id),
            "master_payment_reference": (
                await db.execute(
                    select(Payment.reference).where(Payment.id == receipt.payment_id)
                )
            ).scalar_one(),
            "reviewed_by_auth_id": admin.user_id,
            "review_note": body.review_note,
            "reviewed_at": utc_now().isoformat(),
        },
    }
    payment.status = PaymentStatus.FAILED
    payment.admin_review_note = body.review_note
    payment.entitlement_error = "Superseded by verified shared bank receipt allocation"
    await db.commit()
    return {
        "state": "reconciled",
        "payment_reference": payment.reference,
        "idempotent": False,
    }


class VoidReservedAllocation(BaseModel):
    reason: str = Field(min_length=15, max_length=1000)


@router.post("/{receipt_id}/allocations/{allocation_id}/void")
async def void_unapplied_allocation(
    receipt_id: uuid.UUID,
    allocation_id: uuid.UUID,
    body: VoidReservedAllocation,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Return an unapplied mistaken allocation to the receipt pool.

    An applied tuition credit is never voided here; it requires explicit
    finance reversal so a member cannot silently lose money.
    """
    receipt = (
        await db.execute(
            select(AcademyBankReceipt)
            .where(AcademyBankReceipt.id == receipt_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not receipt:
        raise HTTPException(404, "Bank receipt not found")
    allocation = (
        await db.execute(
            select(AcademyReceiptAllocation)
            .where(
                AcademyReceiptAllocation.id == allocation_id,
                AcademyReceiptAllocation.receipt_id == receipt_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not allocation:
        raise HTTPException(404, "Beneficiary allocation not found")
    if allocation.state == "void":
        return await _receipt_summary(db, receipt)
    if allocation.state != "reserved":
        raise HTTPException(
            409, "Applied tuition credits cannot be voided as unused funds"
        )
    allocation.state = "void"
    allocation.voided_by_auth_id = admin.user_id
    allocation.void_reason = body.reason
    allocation.voided_at = utc_now()
    await db.commit()
    return await _receipt_summary(db, receipt)
