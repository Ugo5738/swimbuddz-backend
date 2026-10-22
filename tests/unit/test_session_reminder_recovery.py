from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from libs.common.datetime_utils import utc_now
from services.communications_service.tasks import session_notifications as tasks
from services.communications_service.models import ScheduledNotificationStatus as Status
from services.communications_service.models import SessionNotificationType as Kind


def row(**updates):
    data = dict(
        notification_type=Kind.REMINDER_24H,
        status=Status.PENDING,
        scheduled_for=utc_now() + timedelta(days=1),
        error_message=None,
        is_short_notice=False,
    )
    data.update(updates)
    return SimpleNamespace(**data)


def database(existing):
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: existing))
    return SimpleNamespace(execute=AsyncMock(return_value=result), add=Mock())


@pytest.mark.asyncio
async def test_recovery_moves_existing_pending_job_and_cancels_duplicates():
    first, duplicate = row(), row()
    candidate = row(scheduled_for=utc_now() + timedelta(days=3))
    db = database([first, duplicate])
    await tasks._upsert_session_reminders(db, uuid4(), [candidate])
    assert first.scheduled_for == candidate.scheduled_for
    assert duplicate.status == Status.CANCELLED
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_preserves_already_sent_job_for_same_time():
    sent = row(status=Status.SENT)
    db = database([sent])
    await tasks._upsert_session_reminders(
        db, uuid4(), [row(scheduled_for=sent.scheduled_for)]
    )
    db.add.assert_not_called()
    assert sent.status == Status.SENT


@pytest.mark.asyncio
async def test_recovery_inserts_missing_job_without_sending_email():
    db = database([])
    candidate = row()
    await tasks._upsert_session_reminders(db, uuid4(), [candidate])
    db.add.assert_called_once_with(candidate)


@pytest.mark.asyncio
async def test_reconciliation_uses_scheduled_sessions_and_current_data(monkeypatch):
    session = {
        "id": str(uuid4()),
        "starts_at": (utc_now() + timedelta(days=2)).isoformat(),
    }
    response = SimpleNamespace(raise_for_status=Mock(), json=lambda: [session])
    fetch = AsyncMock(return_value=response)
    schedule = AsyncMock()
    monkeypatch.setattr(tasks, "internal_get", fetch)
    monkeypatch.setattr(tasks, "schedule_session_notifications", schedule)
    assert await tasks.reconcile_session_reminders() == {"sessions": 1}
    assert fetch.await_args.kwargs["path"] == "/internal/sessions/scheduled"
    assert schedule.await_args.kwargs["session_data"] == session
