"""Regression tests for S3 bandwidth reconciliation cost and audit safety."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.media_service.tasks.vault_bandwidth import (
    S3AccessEvent,
    _best_transfer,
    _is_derivative,
    _retry_delay,
    _retry_unmatched_events,
    reconcile_vault_bandwidth,
)


def _event(key="vaults/clip.mov", occurred=None):
    return S3AccessEvent(
        target_bucket="private",
        request_id="test-1",
        occurred_at=occurred or datetime(2026, 11, 15, tzinfo=timezone.utc),
        object_key=key,
        bytes_sent=1024,
        status_code=206,
        remote_ip="203.0.113.1",
        user_agent="iPhone",
        operation="REST.GET.OBJECT",
        requester=None,
        error_code=None,
        authentication_type="QueryString",
    )


def test_derivative_preview_is_unattributed_delivery_not_original_authorization():
    assert _is_derivative(_event("vault-derivatives/preview.mp4"))
    assert not _is_derivative(_event("vaults/original.mov"))
    assert not _is_derivative(_event("vault-exports/export.zip"))


def test_retry_backoff_increases_and_caps_at_one_day():
    assert _retry_delay(1) == timedelta(hours=1)
    assert _retry_delay(2) == timedelta(hours=2)
    assert _retry_delay(3) == timedelta(hours=4)
    assert _retry_delay(20) == timedelta(days=1)


def test_batch_transfer_scoring_keeps_signed_url_window_and_identity():
    event = _event()
    best = SimpleNamespace(
        created_at=event.occurred_at - timedelta(minutes=10),
        ip_address=event.remote_ip,
        user_agent=event.user_agent,
    )
    later = SimpleNamespace(
        created_at=event.occurred_at - timedelta(minutes=5),
        ip_address="other",
        user_agent="other",
    )
    too_old = SimpleNamespace(
        created_at=event.occurred_at - timedelta(hours=3),
        ip_address=event.remote_ip, user_agent=event.user_agent,
    )
    assert _best_transfer(event, [later, too_old, best], ttl=3600) is best
    assert _best_transfer(event, [too_old], ttl=3600) is None


class _Rows:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


@pytest.mark.asyncio
async def test_retry_uses_one_bulk_candidate_query_and_schedules_next_attempt():
    now = datetime(2026, 11, 16, 6, tzinfo=timezone.utc)
    event = _event(occurred=now - timedelta(days=1))
    stored = SimpleNamespace(
        target_bucket=event.target_bucket,
        request_id=event.request_id,
        occurred_at=event.occurred_at,
        object_key=event.object_key,
        bytes_sent=event.bytes_sent,
        status_code=event.status_code,
        remote_ip=event.remote_ip,
        user_agent=event.user_agent,
        metadata_json={"operation": event.operation},
        match_attempts=0,
        last_match_attempt_at=None,
        next_match_attempt_at=None,
        match_status="unmatched",
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[None, None, None, _Rows([stored]), _Rows([])]),
        scalar=AsyncMock(return_value=None),
        commit=AsyncMock(),
    )

    assert await _retry_unmatched_events(db, now) == 0
    # Three set-based historical classifications, one locked batch scan,
    # and only one transfer lookup for the entire retry batch.
    assert db.execute.await_count == 5
    assert db.scalar.await_count == 1
    assert db.commit.await_count == 1
    assert stored.match_attempts == 1
    assert stored.match_status == "unmatched"
    assert stored.last_match_attempt_at == now
    assert stored.next_match_attempt_at == now + timedelta(hours=1)
    sql = str(db.execute.await_args_list[3].args[0])
    assert "SKIP LOCKED" in sql


@pytest.mark.asyncio
async def test_retry_marks_last_failed_attempt_terminal_without_deleting_event():
    now = datetime(2026, 11, 16, 6, tzinfo=timezone.utc)
    event = _event(occurred=now - timedelta(days=1))
    stored = SimpleNamespace(
        target_bucket=event.target_bucket,
        request_id=event.request_id,
        occurred_at=event.occurred_at,
        object_key=event.object_key,
        bytes_sent=event.bytes_sent,
        status_code=event.status_code,
        remote_ip=event.remote_ip,
        user_agent=event.user_agent,
        metadata_json={},
        match_attempts=6,
        last_match_attempt_at=None,
        next_match_attempt_at=None,
        match_status="unmatched",
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[None, None, None, _Rows([stored]), _Rows([])]),
        scalar=AsyncMock(return_value=None),
        commit=AsyncMock(),
    )
    assert await _retry_unmatched_events(db, now) == 0
    assert stored.match_attempts == 7
    assert stored.match_status == "exhausted"
    assert stored.next_match_attempt_at is None
    assert stored.bytes_sent == event.bytes_sent
    db.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_emergency_pause_does_not_touch_media_storage(monkeypatch):
    from services.media_service.tasks import vault_bandwidth

    monkeypatch.setattr(
        vault_bandwidth.settings,
        "MEDIA_VAULT_BANDWIDTH_RECONCILIATION_ENABLED",
        False,
    )
    assert await reconcile_vault_bandwidth() == {
        "status": "paused",
        "processed_objects": 0,
    }
