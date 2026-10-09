"""Previously approved partial tuition must never be collected or credited again."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from libs.auth.models import AuthUser
from services.payments_service.models import (
    AcademyBankReceipt,
    Payment,
    PaymentPurpose,
    PaymentStatus,
)
from services.payments_service.routers import academy_receipts as receipts

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class IdentityResponse:
    def __init__(self, member_auth_id):
        self.member_auth_id = member_auth_id

    def raise_for_status(self):
        pass

    def json(self):
        return {"member_auth_id": self.member_auth_id, "currency_snapshot": "NGN"}


async def test_retroactive_adoption_books_only_50k_and_protects_first_student(
    db_session, monkeypatch,
):
    enrollment_id = uuid4()
    bank_ref = f"BANK-{uuid4()}"
    old_payment = Payment(
        reference=f"PAY-{uuid4()}",
        member_auth_id="first-swimmer",
        purpose=PaymentPurpose.ACADEMY_COHORT,
        status=PaymentStatus.PAID,
        amount=50000,
        currency="NGN",
        provider="offline",
        provider_reference=bank_ref,
        payment_method="bank_transfer",
        paid_at=receipts.utc_now(),
        entitlement_applied_at=receipts.utc_now(),
        payment_metadata={"enrollment_id": str(enrollment_id)},
    )
    db_session.add(old_payment)
    await db_session.commit()
    emit = AsyncMock()
    get = AsyncMock(return_value=IdentityResponse("first-swimmer"))
    monkeypatch.setattr(receipts, "emit_payment_to_ledger", emit)
    monkeypatch.setattr(receipts, "internal_get", get)
    body = receipts.AdoptSettledAcademyReceiptRequest(
        original_payment_reference=old_payment.reference,
        external_reference=bank_ref,
        actual_bank_amount_kobo=10_000_000,
        reviewed_bank_evidence="Verified bank statement: 100000 received once, 50000 credited to first swimmer only.",
        confirm_original_payment_is_one_beneficiary=True,
        confirm_unrecorded_remainder=True,
    )
    admin = AuthUser(user_id="finance-admin")
    result = await receipts.adopt_previously_settled_academy_receipt(
        body, admin, db_session
    )
    assert result["preexisting_paid_kobo"] == 5_000_000
    assert result["new_cash_kobo"] == 5_000_000
    assert result["allocated_kobo"] == 5_000_000
    assert result["unallocated_kobo"] == 5_000_000
    assert len(result["allocations"]) == 1
    assert result["allocations"][0]["state"] == "historical"
    emit.assert_awaited_once()
    assert emit.await_args.args[1].amount == 50000
    assert old_payment.amount == 50000
    assert old_payment.status == PaymentStatus.PAID
    assert old_payment.entitlement_applied_at is not None

    retry = await receipts.adopt_previously_settled_academy_receipt(
        body, admin, db_session
    )
    assert retry["id"] == result["id"]
    emit.assert_awaited_once()
    rows = (await db_session.execute(select(AcademyBankReceipt))).scalars().all()
    assert len(rows) == 1

    # The historical allocation is not spendable a second time.
    with pytest.raises(HTTPException) as error:
        await receipts.apply_receipt_allocation(
            rows[0].id, rows[0].id, admin, db_session,
        )
    assert error.value.status_code == 404


async def test_adoption_rejects_unverified_cash_claims(db_session, monkeypatch):
    bank_ref = f"BANK-{uuid4()}"
    old_payment = Payment(
        reference=f"PAY-{uuid4()}",
        member_auth_id="student",
        purpose=PaymentPurpose.ACADEMY_COHORT,
        status=PaymentStatus.PAID,
        amount=50000,
        currency="NGN",
        provider="offline",
        provider_reference=bank_ref,
        paid_at=receipts.utc_now(),
        entitlement_applied_at=receipts.utc_now(),
        payment_metadata={"enrollment_id": str(uuid4())},
    )
    db_session.add(old_payment)
    await db_session.commit()
    monkeypatch.setattr(
        receipts, "internal_get",
        AsyncMock(return_value=IdentityResponse("student")),
    )
    candidate = receipts.AdoptSettledAcademyReceiptRequest(
        original_payment_reference=old_payment.reference,
        external_reference=bank_ref,
        actual_bank_amount_kobo=10_000_000,
        reviewed_bank_evidence="Detailed original bank evidence confirming one payment received",
        confirm_original_payment_is_one_beneficiary=True,
        confirm_unrecorded_remainder=False,
    )
    with pytest.raises(HTTPException) as error:
        await receipts.adopt_previously_settled_academy_receipt(
            candidate, AuthUser(user_id="admin"), db_session,
        )
    assert error.value.status_code == 422
    count = (await db_session.execute(select(AcademyBankReceipt))).scalars().all()
    assert not count
