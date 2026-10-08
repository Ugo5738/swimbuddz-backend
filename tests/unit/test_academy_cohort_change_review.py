"""Focused tests for Academy transfer review ownership and state transitions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.academy_service.routers.enrollments.change_cohort import (
    reject_enrollment_change,
)


@pytest.mark.asyncio
async def test_admin_rejection_is_idempotent_and_preserves_enrollment():
    change = SimpleNamespace(
        id=uuid4(),
        state="needs_review",
        snapshot={"payment_references": ["PAY-123"]},
    )
    scalar = SimpleNamespace(scalar_one_or_none=lambda: change)
    db = SimpleNamespace(execute=AsyncMock(return_value=scalar), commit=AsyncMock())
    admin = SimpleNamespace(user_id="admin-1")
    result = await reject_enrollment_change(change.id, admin, db)
    assert result["state"] == "rejected"
    assert change.snapshot["payment_references"] == ["PAY-123"]
    assert change.snapshot["reviewed_by_auth_id"] == "admin-1"
    db.commit.assert_awaited_once()
    await reject_enrollment_change(change.id, admin, db)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_transfer_cannot_be_rejected():
    change = SimpleNamespace(id=uuid4(), state="completed", snapshot={})
    scalar = SimpleNamespace(scalar_one_or_none=lambda: change)
    db = SimpleNamespace(execute=AsyncMock(return_value=scalar), commit=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await reject_enrollment_change(change.id, SimpleNamespace(user_id="admin"), db)
    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_or_unclosed_attempts_block_admin_approval(monkeypatch):
    from services.academy_service.models import EnrollmentStatus, PaymentStatus
    from services.academy_service.routers.enrollments import change_cohort

    enrollment = SimpleNamespace(
        id=uuid4(),
        status=EnrollmentStatus.PENDING_APPROVAL,
        payment_status=PaymentStatus.PENDING,
        paid_at=None,
        progress_records=[],
        installments=[],
    )
    change = SimpleNamespace(
        id=uuid4(),
        state="needs_review",
        from_enrollment_id=enrollment.id,
    )
    responses = iter(
        [
            SimpleNamespace(scalar_one_or_none=lambda: change),
            SimpleNamespace(scalar_one_or_none=lambda: enrollment),
        ]
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=lambda *_: next(responses)),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        change_cohort,
        "financial_state",
        AsyncMock(
            return_value={
                "has_payment_activity": True,
                "all_unpaid_closed": False,
                "references": ["PAY-UNRECONCILED"],
            }
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await change_cohort.approve_unpaid_enrollment_change(
            change.id,
            change_cohort.ApproveUnpaidCohortChange(reason="Verified review attempt"),
            SimpleNamespace(user_id="admin"),
            db,
        )
    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()
