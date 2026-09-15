import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.payments_service.models import PaymentStatus
from services.payments_service.routers import manual
from services.payments_service.schemas import (
    MemberPaymentResponse,
    PaymentResponse,
    SubmitProofRequest,
)
from tests.conftest import make_member_user, make_admin_user


def payment():
    return SimpleNamespace(
        id=uuid.uuid4(),
        reference="TRANSFER-TEST",
        member_auth_id="test-member-id",
        purpose="club",
        amount=20000,
        currency="NGN",
        status=PaymentStatus.PENDING,
        payment_method="manual_transfer",
        proof_of_payment_media_id=None,
        admin_review_note="Internal note",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


async def test_submit_receipt_serializes_and_same_receipt_retry_succeeds(monkeypatch):
    row = payment()
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row)),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    monkeypatch.setattr(manual, "_set_pending_tier_payment_for_payment", AsyncMock())
    proof = SubmitProofRequest(proof_media_id=str(uuid.uuid4()))
    saved = await manual.submit_proof_of_payment(
        row.reference, proof, make_member_user(), db
    )
    member = MemberPaymentResponse.model_validate(saved).model_dump(mode="json")
    admin = PaymentResponse.model_validate(saved).model_dump(mode="json")
    assert member["proof_of_payment_media_id"] == str(proof.proof_media_id)
    assert member["status"] == "pending_review"
    assert "admin_review_note" not in member
    assert admin["proof_of_payment_media_id"] == member["proof_of_payment_media_id"]
    await manual.submit_proof_of_payment(row.reference, proof, make_member_user(), db)
    db.commit.assert_awaited_once()
    with pytest.raises(HTTPException):
        await manual.submit_proof_of_payment(
            row.reference,
            SubmitProofRequest(proof_media_id=uuid.uuid4()),
            make_member_user(),
            db,
        )


def test_invalid_receipt_id_rejected_before_persistence():
    with pytest.raises(ValidationError):
        SubmitProofRequest(proof_media_id="not-a-media-id")


async def test_admin_review_list_with_uploaded_uuid_can_be_serialized():
    row = payment()
    row.proof_of_payment_media_id = uuid.uuid4()
    row.status = PaymentStatus.PENDING_REVIEW
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [row])
            )
        )
    )
    rows = await manual.list_pending_review_payments(make_admin_user(), db)
    assert PaymentResponse.model_validate(rows[0]).model_dump(mode="json")[
        "proof_of_payment_media_id"
    ] == str(row.proof_of_payment_media_id)


@pytest.mark.parametrize(
    "status,method",
    [(PaymentStatus.PAID, "manual_transfer"), (PaymentStatus.PENDING, "paystack")],
)
async def test_receipt_cannot_overwrite_paid_or_online_payment(status, method):
    row = payment()
    row.status, row.payment_method = status, method
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row)),
        commit=AsyncMock(),
    )
    with pytest.raises(HTTPException):
        await manual.submit_proof_of_payment(
            row.reference,
            SubmitProofRequest(proof_media_id=uuid.uuid4()),
            make_member_user(),
            db,
        )
    db.commit.assert_not_awaited()
