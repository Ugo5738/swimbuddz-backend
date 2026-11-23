"""Regression tests for Supabase egress controls across background workers and reporting."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.academy_service.tasks.worker import WorkerSettings as AcademyWorker
from services.attendance_service.services import academy_defaults
from services.attendance_service.worker import (
    RECENT_ACADEMY_DEFAULTS_LOOKBACK_HOURS,
    WorkerSettings as AttendanceWorker,
)
from services.communications_service.worker import (
    WorkerSettings as CommunicationsWorker,
)
from services.events_service.tasks.worker import WorkerSettings as EventsWorker
from services.media_service.tasks import vault_lifecycle
from services.media_service.tasks.worker import WorkerSettings as MediaWorker
from services.members_service.routers import quarterly_metrics as members_qm
from services.members_service.tasks.worker import WorkerSettings as MembersWorker
from services.payments_service.worker import WorkerSettings as PaymentsWorker
from services.reporting_service.services import aggregator
from services.reporting_service.tasks.worker import WorkerSettings as ReportingWorker
from services.sessions_service.worker import WorkerSettings as SessionsWorker
from services.transport_service.tasks.worker import WorkerSettings as TransportWorker


def _cron(worker_cls, task_name: str):
    return next(job for job in worker_cls.cron_jobs if job.name == f"cron:{task_name}")


def test_reporting_worker_refreshes_current_quarter_twice_daily():
    job = _cron(ReportingWorker, "task_refresh_current_quarter_reports")
    assert job.hour == {2, 19}
    assert job.minute == 10
    assert job.run_at_startup is False


def test_attendance_worker_splits_recent_window_and_daily_history_repair():
    recent = _cron(AttendanceWorker, "task_reconcile_recent_academy_defaults")
    assert recent.minute == {0, 15, 30, 45}
    assert recent.hour is None
    assert recent.run_at_startup is False

    daily = _cron(AttendanceWorker, "task_reconcile_academy_defaults")
    assert daily.hour == {2}
    assert daily.minute == {15}
    assert daily.run_at_startup is True

    rewards = _cron(AttendanceWorker, "task_sync_swim_rewards")
    assert rewards.minute == {2, 12, 22, 32, 42, 52}
    assert rewards.run_at_startup is True
    assert RECENT_ACADEMY_DEFAULTS_LOOKBACK_HOURS == 48


def test_high_frequency_and_safety_net_worker_schedules_are_bounded():
    assert _cron(CommunicationsWorker, "task_send_operational_emails").minute == {
        0,
        5,
        10,
        15,
        20,
        25,
        30,
        35,
        40,
        45,
        50,
        55,
    }
    assert _cron(CommunicationsWorker, "task_process_pending_notifications").minute == {
        2,
        17,
        32,
        47,
    }

    assert _cron(PaymentsWorker, "task_reconcile_pending_payments").minute == {
        0,
        15,
        30,
        45,
    }
    assert _cron(PaymentsWorker, "task_retry_payment_fulfillment").minute == {
        3,
        18,
        33,
        48,
    }
    assert _cron(PaymentsWorker, "task_reconcile_membership_payment_state").minute == {
        6,
        36,
    }
    assert _cron(PaymentsWorker, "task_send_pending_admin_payment_emails").minute == {
        9,
        19,
        29,
        39,
        49,
        59,
    }

    assert _cron(SessionsWorker, "task_retry_club_reschedule_notifications").minute == {
        3,
        18,
        33,
        48,
    }
    assert _cron(SessionsWorker, "task_retry_booking_confirmations").minute == {
        6,
        21,
        36,
        51,
    }
    assert _cron(SessionsWorker, "task_sweep_expired_pending_bookings").minute == {
        0,
        10,
        20,
        30,
        40,
        50,
    }

    assert _cron(MediaWorker, "task_reconcile_vault_bandwidth").minute == 15
    assert _cron(MediaWorker, "task_backfill_vault_video_previews").minute == 32
    vault_sync = _cron(MediaWorker, "task_sync_session_vaults")
    assert vault_sync.hour == {0, 4, 8, 12, 16, 20}
    assert vault_sync.minute == 5

    assert _cron(EventsWorker, "task_send_due_event_reminders").minute == {
        0,
        15,
        30,
        45,
    }

    for worker_cls, task_name in [
        (EventsWorker, "task_reconcile_chat_memberships"),
        (AcademyWorker, "task_reconcile_chat_memberships"),
        (AcademyWorker, "task_reconcile_academy_membership_projections"),
        (AcademyWorker, "task_reconcile_extension_payout_schedules"),
        (MembersWorker, "task_reconcile_chat_memberships"),
        (TransportWorker, "task_reconcile_chat_memberships"),
    ]:
        job = _cron(worker_cls, task_name)
        assert job.hour == {0, 6, 12, 18}
        assert job.run_at_startup is True


@pytest.mark.asyncio
async def test_reconcile_academy_defaults_skips_student_and_clearance_calls_when_no_recent_sessions(
    monkeypatch,
):
    now = datetime(2026, 11, 23, 16, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(academy_defaults, "utc_now", lambda: now)

    calls: list[tuple[str, dict | None]] = []

    async def fake_internal_get(*, service_url, path, calling_service, params=None):
        calls.append((path, params))
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if path == "/internal/academy/cohorts":
            resp.json.return_value = {"cohorts": [{"id": "cohort-1"}]}
        elif path == "/internal/sessions/cohorts/cohort-1/sessions":
            resp.json.return_value = [
                # Paid-extra or future sessions are not eligible for defaults
                {
                    "id": str(uuid.uuid4()),
                    "session_type": "cohort_class",
                    "status": "scheduled",
                    "cohort_fee_mode": "paid_extra",
                    "starts_at": (now - timedelta(hours=2)).isoformat(),
                }
            ]
        else:
            raise AssertionError(f"Unexpected internal_get: {path}")
        return resp

    internal_post = AsyncMock()
    db_factory = MagicMock()
    monkeypatch.setattr(academy_defaults, "internal_get", fake_internal_get)
    monkeypatch.setattr(academy_defaults, "internal_post", internal_post)
    monkeypatch.setattr(academy_defaults, "AsyncSessionLocal", db_factory)

    result = await academy_defaults.reconcile_academy_defaults(lookback_hours=48)

    assert result["created"] == 0
    assert result["lookback_hours"] == 48
    assert calls[1][0] == "/internal/sessions/cohorts/cohort-1/sessions"
    assert calls[1][1] == {
        "start_date": (now - timedelta(hours=48)).isoformat(),
        "end_date": now.isoformat(),
    }
    internal_post.assert_not_awaited()
    db_factory.assert_not_called()


@pytest.mark.asyncio
async def test_reporting_segment_attendance_reuses_cohort_sessions_cache(monkeypatch):
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(
        aggregator, "utc_now", lambda: datetime(2026, 11, 23, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(
        aggregator, "_fetch_member_session_commitments", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        aggregator,
        "_fetch_member_cohort_enrollments",
        AsyncMock(
            return_value=[
                {
                    "cohort_id": "cohort-1",
                    "enrolled_at": "2026-10-01T00:00:00+00:00",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        aggregator, "_fetch_converted_guest_hours", AsyncMock(return_value=0.0)
    )
    fetch_cohort = AsyncMock(
        return_value=[
            {
                "id": "sess-1",
                "session_type": "cohort_class",
                "starts_at": "2026-10-10T08:00:00+00:00",
                "ends_at": "2026-10-10T09:00:00+00:00",
            }
        ]
    )
    monkeypatch.setattr(aggregator, "_fetch_cohort_sessions", fetch_cohort)
    monkeypatch.setattr(
        aggregator,
        "_fetch_member_attendance_records",
        AsyncMock(return_value=[{"session_id": "sess-1", "status": "present"}]),
    )

    cache: dict[str, list[dict]] = {}
    for idx in range(2):
        summary = await aggregator._compute_segment_attendance(
            member_auth_id=f"auth-{idx}",
            member_id=f"member-{idx}",
            start=start,
            end=end,
            fallback_attendance={"attendance_available": True, "attended_sessions": []},
            cohort_sessions_cache=cache,
        )
        assert summary["total_attended"] == 1

    assert fetch_cohort.await_count == 1


@pytest.mark.asyncio
async def test_compute_member_report_skips_duplicate_wallet_summary_when_extensions_present(
    monkeypatch,
):
    monkeypatch.setattr(
        aggregator, "_fetch_attendance_stats", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        aggregator, "_fetch_payment_summary", AsyncMock(return_value={"total_spent": 0})
    )
    fetch_wallet_fallback = AsyncMock(
        return_value={"bubbles_earned": 99, "bubbles_spent": 11}
    )
    monkeypatch.setattr(aggregator, "_fetch_wallet_summary", fetch_wallet_fallback)
    monkeypatch.setattr(
        aggregator, "_fetch_academy_summary", AsyncMock(return_value={})
    )
    monkeypatch.setattr(aggregator, "_fetch_store_summary", AsyncMock(return_value={}))
    monkeypatch.setattr(
        aggregator, "_fetch_volunteer_summary", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        aggregator, "_fetch_transport_summary", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        aggregator,
        "fetch_member_extensions",
        AsyncMock(
            return_value={
                "members": {"available": True, "metrics": {}, "details": {}},
                "wallet": {
                    "available": True,
                    "metrics": {"wallet_credits": 5, "wallet_debits": 2},
                    "details": {},
                },
            }
        ),
    )
    monkeypatch.setattr(
        aggregator,
        "_compute_segment_attendance",
        AsyncMock(
            return_value={
                "primary_segment": "community",
                "total_attended": 0,
                "total_available": 0,
                "attendance_rate": 0.0,
                "punctuality_rate": 0.0,
                "weekly_attendance": [],
                "pool_hours": 0.0,
                "sessions_by_type": {},
            }
        ),
    )

    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = None
    db = MagicMock()
    db.execute = AsyncMock(return_value=existing_result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    report = await aggregator.compute_member_report(
        member_auth_id="auth-1",
        year=2026,
        quarter=4,
        db=db,
        member_info={"id": str(uuid.uuid4()), "first_name": "Ada", "last_name": "Swim"},
    )

    fetch_wallet_fallback.assert_not_awaited()
    assert report.bubbles_earned == 5
    assert report.bubbles_spent == 2


@pytest.mark.asyncio
async def test_members_reporting_summary_skips_club_and_pod_queries_for_inactive_member():
    member_id = uuid.uuid4()
    executed_sql: list[str] = []

    class ScalarResult:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

        def scalars(self):
            return self

        def all(self):
            return self._value

    results = iter(
        [
            ScalarResult(member_id),  # Member.id lookup
            ScalarResult([]),  # PodAssignment
            ScalarResult([]),  # ClubEnrollment
            ScalarResult([]),  # CommunityExperienceOrder
            ScalarResult([]),  # CommunityExperiencePurchase
            ScalarResult([]),  # CommunityExperienceAttendance
        ]
    )

    class FakeDB:
        async def execute(self, statement):
            executed_sql.append(str(statement))
            return next(results)

    section = await members_qm.get_reporting_summary(
        date_from=datetime(2026, 10, 1, tzinfo=timezone.utc),
        date_to=datetime(2026, 12, 31, tzinfo=timezone.utc),
        member_auth_id="auth-inactive",
        _=MagicMock(),
        db=FakeDB(),
    )

    assert len(executed_sql) == 6
    assert not any("FROM clubs" in sql for sql in executed_sql)
    assert not any("FROM pods" in sql for sql in executed_sql)
    assert section.metrics["clubs_configured"] == 0
    assert section.metrics["pods_configured"] == 0
    assert section.details["clubs"] == []
    assert section.details["pods"] == []


@pytest.mark.asyncio
async def test_members_reporting_summary_scopes_club_and_pod_queries_for_active_member():
    member_id = uuid.uuid4()
    club_id = uuid.uuid4()
    pod_id = uuid.uuid4()
    executed_sql: list[str] = []

    class ScalarResult:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

        def scalars(self):
            return self

        def all(self):
            return self._value

    assignment = SimpleNamespace(member_id=member_id, pod_id=pod_id, left_at=None)
    enrollment = SimpleNamespace(
        member_id=member_id, club_id=club_id, payment_mode="quarterly_prepaid"
    )
    club = SimpleNamespace(
        id=club_id,
        name="VI Club",
        location="Victoria Island",
        default_pool_id=None,
        operating_area_id=None,
        is_active=True,
    )
    pod = SimpleNamespace(id=pod_id, name="Pod A", club_id=club_id)

    results = iter(
        [
            ScalarResult(member_id),  # Member.id
            ScalarResult([assignment]),  # PodAssignment
            ScalarResult([enrollment]),  # ClubEnrollment
            ScalarResult([club_id]),  # Pod.club_id lookup
            ScalarResult([club]),  # Scoped Club lookup
            ScalarResult([pod]),  # Scoped Pod lookup
            ScalarResult([]),  # CommunityExperienceOrder
            ScalarResult([]),  # CommunityExperiencePurchase
            ScalarResult([]),  # CommunityExperienceAttendance
        ]
    )

    class FakeDB:
        async def execute(self, statement):
            executed_sql.append(str(statement))
            return next(results)

    section = await members_qm.get_reporting_summary(
        date_from=datetime(2026, 10, 1, tzinfo=timezone.utc),
        date_to=datetime(2026, 12, 31, tzinfo=timezone.utc),
        member_auth_id="auth-active",
        _=MagicMock(),
        db=FakeDB(),
    )

    assert any("clubs.id IN" in sql for sql in executed_sql)
    assert any("pods.club_id IN" in sql for sql in executed_sql)
    assert section.metrics["clubs_configured"] == 1
    assert section.metrics["pods_configured"] == 1
    assert section.metrics["pods_populated_at_end"] == 1
    assert len(section.details["clubs"]) == 1
    assert len(section.details["pods"]) == 1


@pytest.mark.asyncio
async def test_reconcile_academy_defaults_full_history_omits_start_date(monkeypatch):
    now = datetime(2026, 11, 23, 2, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(academy_defaults, "utc_now", lambda: now)

    seen_params: list[dict | None] = []

    async def fake_internal_get(*, service_url, path, calling_service, params=None):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if path == "/internal/academy/cohorts":
            resp.json.return_value = {"cohorts": [{"id": "cohort-1"}]}
        elif path == "/internal/sessions/cohorts/cohort-1/sessions":
            seen_params.append(params)
            resp.json.return_value = []
        return resp

    monkeypatch.setattr(academy_defaults, "internal_get", fake_internal_get)

    result = await academy_defaults.reconcile_academy_defaults()

    assert result["lookback_hours"] is None
    assert seen_params == [{"end_date": now.isoformat()}]


@pytest.mark.asyncio
async def test_vault_sync_skips_volunteer_grant_refresh_for_closed_vaults(monkeypatch):
    now = datetime(2026, 11, 23, 16, 0, tzinfo=timezone.utc)
    open_session_id = uuid.uuid4()
    closed_session_id = uuid.uuid4()

    open_vault = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=open_session_id,
        deleted_at=None,
        status="open",
        upload_closes_at=now + timedelta(hours=24),
    )
    closed_vault = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=closed_session_id,
        deleted_at=None,
        status="review",
        upload_closes_at=now - timedelta(hours=24),
    )
    vaults = iter([open_vault, closed_vault])

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def scalar(self, statement):
            return next(vaults)

        async def commit(self):
            return None

        async def rollback(self):
            return None

    monkeypatch.setattr(vault_lifecycle, "utc_now", lambda: now)
    monkeypatch.setattr(
        vault_lifecycle,
        "list_scheduled_sessions",
        AsyncMock(
            return_value=[
                {
                    "id": str(open_session_id),
                    "session_type": "club",
                    "title": "Open Swim",
                    "starts_at": (now - timedelta(hours=2)).isoformat(),
                    "ends_at": (now - timedelta(hours=1)).isoformat(),
                },
                {
                    "id": str(closed_session_id),
                    "session_type": "club",
                    "title": "Closed Swim",
                    "starts_at": (now - timedelta(days=5)).isoformat(),
                    "ends_at": (
                        now - timedelta(days=5) + timedelta(hours=1)
                    ).isoformat(),
                },
            ]
        ),
    )
    monkeypatch.setattr(vault_lifecycle, "AsyncSessionLocal", lambda: FakeSession())
    sync_grants = AsyncMock()
    monkeypatch.setattr(vault_lifecycle, "sync_volunteer_grants", sync_grants)

    result = await vault_lifecycle.sync_session_vaults()

    assert result == {"status": "ok", "created": 0, "existing": 2, "failed": 0}
    sync_grants.assert_awaited_once()
    assert sync_grants.await_args.kwargs == {
        "vault": open_vault,
        "created_by": None,
    }
