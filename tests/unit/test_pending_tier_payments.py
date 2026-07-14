from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from services.payments_service.models import PaymentPurpose
from services.payments_service.routers.intents import _helpers
from services.payments_service import tasks


def _payment(purpose: PaymentPurpose):
    return SimpleNamespace(
        purpose=purpose,
        member_auth_id="auth-1",
        reference="PAY-123",
    )


@pytest.mark.asyncio
async def test_only_membership_purposes_set_pending_tier_state(monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(_helpers, "_update_pending_tier_payment", update)

    await _helpers._set_pending_tier_payment_for_payment(_payment(PaymentPurpose.CLUB))
    await _helpers._set_pending_tier_payment_for_payment(
        _payment(PaymentPurpose.SESSION_BUNDLE)
    )

    update.assert_awaited_once_with(
        "auth-1",
        tier="club",
        reference="PAY-123",
    )


@pytest.mark.asyncio
async def test_pending_tier_clear_is_reference_guarded(monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(_helpers, "_update_pending_tier_payment", update)

    await _helpers._clear_pending_tier_payment_for_payment(
        _payment(PaymentPurpose.ACADEMY_COHORT)
    )

    update.assert_awaited_once_with(
        "auth-1",
        tier="academy",
        reference=None,
        expected_reference="PAY-123",
        required=False,
    )


@pytest.mark.asyncio
async def test_reconciliation_projects_latest_active_and_marks_terminal_clears(
    monkeypatch,
):
    newest = _payment(PaymentPurpose.CLUB)
    newest.id = uuid.uuid4()
    older = _payment(PaymentPurpose.CLUB)
    older.id = uuid.uuid4()
    older.reference = "PAY-OLDER"
    terminal = _payment(PaymentPurpose.ACADEMY_COHORT)
    terminal.id = uuid.uuid4()

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def __init__(self, results):
            self.results = list(results)
            self.executed = []
            self.committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement):
            self.executed.append(statement)
            return self.results.pop(0) if self.results else None

        async def commit(self):
            self.committed = True

    read_session = Session([Result([newest, older]), Result([terminal])])
    write_session = Session([])
    sessions = iter([read_session, write_session])
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: next(sessions))
    set_pending = AsyncMock(return_value=True)
    clear_pending = AsyncMock(return_value=True)
    monkeypatch.setattr(tasks, "_set_pending_tier_payment_for_payment", set_pending)
    monkeypatch.setattr(tasks, "_clear_pending_tier_payment_for_payment", clear_pending)

    await tasks.reconcile_pending_membership_payment_state()

    set_pending.assert_awaited_once_with(newest)
    clear_pending.assert_awaited_once_with(terminal)
    assert len(write_session.executed) == 1
    assert write_session.committed is True
