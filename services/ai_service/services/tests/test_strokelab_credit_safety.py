"""Regression tests for public StrokeLab credit ownership and settlement.

Mock account locking and the ledger lookup; these tests do not require a DB.
"""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.ai_service.services import credit_ops


def _account(*, reserved=1, remaining=0, free_used=True):
    return SimpleNamespace(
        id=uuid.uuid4(), email="buyer@example.com",
        reserved_credits=reserved, remaining_credits=remaining,
        lifetime_spent=0, free_used=free_used, flagged_at=None,
    )


@pytest.mark.asyncio
async def test_refunded_job_cannot_consume_another_jobs_reservation(monkeypatch):
    job_id = uuid.uuid4()
    account = _account(reserved=1)
    db = SimpleNamespace(add=MagicMock())
    monkeypatch.setattr(credit_ops, "_lock_account", AsyncMock(return_value=account))
    monkeypatch.setattr(credit_ops, "find_reservation", AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())))

    async def existing(_db, key):
        return SimpleNamespace(id=uuid.uuid4()) if key == f"refund-{job_id}" else None

    monkeypatch.setattr(credit_ops, "_existing", existing)
    result = await credit_ops.consume_reservation(db, raw_email=account.email, job_id=job_id)
    assert result is None
    assert account.reserved_credits == 1
    assert account.lifetime_spent == 0
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_consumed_job_cannot_refund_another_jobs_reservation(monkeypatch):
    job_id = uuid.uuid4()
    account = _account(reserved=1)
    db = SimpleNamespace(add=MagicMock())
    monkeypatch.setattr(credit_ops, "_lock_account", AsyncMock(return_value=account))
    monkeypatch.setattr(credit_ops, "find_reservation", AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())))

    async def existing(_db, key):
        return SimpleNamespace(id=uuid.uuid4()) if key == f"consume-{job_id}" else None

    monkeypatch.setattr(credit_ops, "_existing", existing)
    result = await credit_ops.refund_reservation(db, raw_email=account.email, job_id=job_id)
    assert result is None
    assert account.reserved_credits == 1
    assert account.remaining_credits == 0
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_paid_reservation_requires_verified_account_under_lock(monkeypatch):
    job_id = uuid.uuid4()
    account = _account(reserved=0, remaining=2, free_used=True)
    db = SimpleNamespace(add=MagicMock())
    monkeypatch.setattr(credit_ops, "_lock_account", AsyncMock(return_value=account))
    with pytest.raises(credit_ops.PaidVerificationRequired):
        await credit_ops.acquire_for_submit(
            db, raw_email=account.email, job_id=job_id, paid_authorized=False
        )
    assert account.remaining_credits == 2
    assert account.reserved_credits == 0
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_matching_reservation_consumes_exactly_once(monkeypatch):
    job_id = uuid.uuid4()
    account = _account(reserved=2)
    db = SimpleNamespace(add=MagicMock())
    monkeypatch.setattr(credit_ops, "_lock_account", AsyncMock(return_value=account))
    monkeypatch.setattr(credit_ops, "find_reservation", AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())))
    monkeypatch.setattr(credit_ops, "_existing", AsyncMock(return_value=None))
    first = await credit_ops.consume_reservation(db, raw_email=account.email, job_id=job_id)
    assert first is not None
    assert account.reserved_credits == 1
    assert account.lifetime_spent == 1
    db.add.assert_called_once()
