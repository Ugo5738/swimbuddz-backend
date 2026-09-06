from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.academy_service.models import EnrollmentStatus, PaymentStatus
from services.academy_service.routers import _shared


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot,expected", [(15_000_000, 15_000_000), (None, 18_000_000)]
)
async def test_installment_schedule_uses_frozen_price(monkeypatch, snapshot, expected):
    enrollment = SimpleNamespace(
        id=uuid4(),
        status=EnrollmentStatus.PENDING_APPROVAL,
        uses_installments=False,
        payment_status=PaymentStatus.PENDING,
        price_snapshot_amount=snapshot,
        currency_snapshot="NGN",
        enrolled_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
    )
    cohort = SimpleNamespace(
        installment_plan_enabled=True,
        installment_count=3,
        installment_deposit_amount=6_000_000,
        price_override=18_000_000,
        start_date=datetime(2026, 10, 3, tzinfo=timezone.utc),
    )
    program = SimpleNamespace(
        price_amount=20_000_000, currency="NGN", duration_weeks=12
    )
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    monkeypatch.setattr(
        _shared, "_list_enrollment_installments", AsyncMock(return_value=[])
    )
    await _shared._ensure_installment_plan(
        db, enrollment, program, cohort, use_installments=True
    )
    installments = [call.args[0] for call in db.add.call_args_list]
    assert len(installments) == 3
    assert sum(item.amount for item in installments) == expected
    assert enrollment.price_snapshot_amount == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("already_opted_in", [False, True])
async def test_waitlist_never_creates_installments(monkeypatch, already_opted_in):
    enrollment = SimpleNamespace(
        id=uuid4(), status=EnrollmentStatus.WAITLIST, uses_installments=already_opted_in
    )
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    monkeypatch.setattr(
        _shared, "_list_enrollment_installments", AsyncMock(return_value=[])
    )
    result = await _shared._ensure_installment_plan(
        db,
        enrollment,
        None,
        SimpleNamespace(installment_plan_enabled=True),
        use_installments=True,
    )
    assert result == []
    assert enrollment.uses_installments is already_opted_in
    db.add.assert_not_called()
