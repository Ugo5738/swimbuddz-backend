"""Shared bank receipts must never be recorded as two cash payments."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from libs.auth.models import AuthUser
from services.payments_service.routers import academy_receipts as service

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_shared_receipt_allocates_only_verified_total(db_session, monkeypatch):
    admin = AuthUser(user_id="admin")
    emit = AsyncMock()
    monkeypatch.setattr(service, "emit_payment_to_ledger", emit)
    total = 100000 * 100
    first = 50000 * 100
    external_reference = f"BANK-{uuid4()}"
    receipt = await service.verify_bank_receipt(
        service.VerifyReceiptRequest(
            external_reference=external_reference,
            amount_kobo=total,
            verification_note="Bank statement confirmed this one deposit exactly",
        ),
        admin,
        db_session,
    )
    assert receipt["unallocated_kobo"] == total
    emit.assert_awaited_once()
    from services.payments_service.models import PaymentPurpose
    assert emit.await_args.args[1].purpose is PaymentPurpose.ACADEMY_COHORT
    monkeypatch.setattr(
        service,
        "internal_get",
        AsyncMock(
            return_value=type(
                "Response",
                (),
                {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: {
                        "member_auth_id": "member",
                        "currency_snapshot": "NGN",
                    },
                },
            )()
        ),
    )
    receipt_id = UUID(receipt["id"])
    first_allocation = service.AllocateReceiptRequest(
        enrollment_id=uuid4(),
        amount_kobo=first,
        idempotency_key="first-member-installment",
    )
    a = await service.allocate_receipt(receipt_id, first_allocation, admin, db_session)
    assert a["allocated_kobo"] == first
    duplicate = await service.allocate_receipt(
        receipt_id, first_allocation, admin, db_session
    )
    assert duplicate["allocated_kobo"] == first
    second = await service.allocate_receipt(
        receipt_id,
        service.AllocateReceiptRequest(
            member_auth_id="member",
            enrollment_id=uuid4(),
            amount_kobo=first,
            idempotency_key="second-member-installment",
        ),
        admin,
        db_session,
    )
    assert second["unallocated_kobo"] == 0
    with pytest.raises(HTTPException) as exc:
        await service.allocate_receipt(
            receipt_id,
            service.AllocateReceiptRequest(
                member_auth_id="member",
                enrollment_id=uuid4(),
                amount_kobo=100,
                idempotency_key="third-over-allocation",
            ),
            admin,
            db_session,
        )
    assert exc.value.status_code == 409


async def test_shared_receipt_reconciliation_preserves_original_proof(db_session):
    from services.payments_service.models import (
        AcademyBankReceipt,
        AcademyReceiptAllocation,
        Payment,
        PaymentPurpose,
        PaymentStatus,
    )

    enrollment_id = uuid4()
    media_id = uuid4()
    master = Payment(
        reference=f"master-{uuid4()}",
        member_auth_id="shared-academy-bank-receipt",
        purpose=PaymentPurpose.ACADEMY_COHORT,
        status=PaymentStatus.PAID,
        amount=100000,
        currency="NGN",
        provider="offline",
        provider_reference=f"BANK-{uuid4()}".upper(),
        entitlement_applied_at=service.utc_now(),
        payment_metadata={"academy_shared_bank_receipt_master": True},
    )
    db_session.add(master)
    await db_session.flush()
    receipt = AcademyBankReceipt(
        payment_id=master.id,
        external_reference=master.provider_reference,
        amount_kobo=10_000_000,
        currency="NGN",
        verification_note="Verified one bank cash-in of 100000 NGN",
        verified_by_auth_id="admin",
    )
    db_session.add(receipt)
    await db_session.flush()
    db_session.add(
        AcademyReceiptAllocation(
            receipt_id=receipt.id,
            enrollment_id=enrollment_id,
            member_auth_id="student",
            amount_kobo=5_000_000,
            state="applied",
            idempotency_key="student-credit",
            created_by_auth_id="admin",
        )
    )
    legacy = Payment(
        reference=f"old-checkout-{uuid4()}",
        member_auth_id="student",
        purpose=PaymentPurpose.ACADEMY_COHORT,
        status=PaymentStatus.PENDING_REVIEW,
        amount=235000,
        currency="NGN",
        proof_of_payment_media_id=media_id,
        payment_method="manual_transfer",
        payment_metadata={
            "enrollment_id": str(enrollment_id),
            "submitted_transfer": {"external_reference": master.provider_reference},
        },
    )
    db_session.add(legacy)
    await db_session.commit()
    admin = AuthUser(user_id="admin")
    body = service.ReconcileLegacyAttempt(
        payment_reference=legacy.reference,
        review_note="Verified this proof matches the one bank receipt assigned to the learner",
    )
    result = await service.link_superseded_academy_checkout_to_shared_receipt(
        receipt.id,
        body,
        admin,
        db_session,
    )
    assert result["state"] == "reconciled"
    assert legacy.status == PaymentStatus.FAILED
    assert legacy.proof_of_payment_media_id == media_id
    assert legacy.payment_metadata["superseded_by_shared_receipt"]["receipt_id"] == str(
        receipt.id
    )
    again = await service.link_superseded_academy_checkout_to_shared_receipt(
        receipt.id,
        body,
        admin,
        db_session,
    )
    assert again["idempotent"] is True
