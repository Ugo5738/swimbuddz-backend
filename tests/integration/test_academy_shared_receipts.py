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
        admin, db_session,
    )
    assert receipt["unallocated_kobo"] == total
    emit.assert_awaited_once()
    monkeypatch.setattr(
        service,
        "internal_get",
        AsyncMock(return_value=type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"member_auth_id": "member", "currency_snapshot": "NGN"},
        })()),
    )
    receipt_id = UUID(receipt["id"])
    first_allocation = service.AllocateReceiptRequest(
        enrollment_id=uuid4(),
        amount_kobo=first, idempotency_key="first-member-installment",
    )
    a = await service.allocate_receipt(receipt_id, first_allocation, admin, db_session)
    assert a["allocated_kobo"] == first
    duplicate = await service.allocate_receipt(receipt_id, first_allocation, admin, db_session)
    assert duplicate["allocated_kobo"] == first
    second = await service.allocate_receipt(
        receipt_id,
        service.AllocateReceiptRequest(
            member_auth_id="member", enrollment_id=uuid4(),
            amount_kobo=first, idempotency_key="second-member-installment",
        ),
        admin, db_session,
    )
    assert second["unallocated_kobo"] == 0
    with pytest.raises(HTTPException) as exc:
        await service.allocate_receipt(
            receipt_id,
            service.AllocateReceiptRequest(
                member_auth_id="member", enrollment_id=uuid4(),
                amount_kobo=100, idempotency_key="third-over-allocation",
            ),
            admin, db_session,
        )
    assert exc.value.status_code == 409
