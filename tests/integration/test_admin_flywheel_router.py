"""Integration tests for admin flywheel endpoints on reporting_service.

Endpoints under test (mounted at ``/admin/reports/flywheel/*``, fronted by
gateway at ``/api/v1/admin/reports/flywheel/*``):
    GET  /overview
    GET  /cohorts?status=&sort=
    GET  /funnel?funnel_stage=&cohort_period=&limit=
    GET  /wallet
    POST /refresh

Auth is overridden to admin in the ``reporting_client`` fixture (see
``tests/conftest.py``).
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete

from services.reporting_service.models import (
    CohortFillSnapshot,
    FunnelConversionSnapshot,
    FunnelStage,
    WalletEcosystemSnapshot,
)


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest_asyncio.fixture(autouse=True)
async def _clean_flywheel_tables(db_session):
    """Clear pre-existing flywheel rows so tests see only what they create.

    Runs inside the per-test transaction, so the rollback at teardown
    restores any rows committed prior to the test (e.g. from a prior
    real refresh against the dev DB).
    """
    await db_session.execute(delete(CohortFillSnapshot))
    await db_session.execute(delete(FunnelConversionSnapshot))
    await db_session.execute(delete(WalletEcosystemSnapshot))
    await db_session.flush()
    yield


# ---------------------------------------------------------------------------
# /overview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_overview_no_snapshots_returns_empty(reporting_client):
    """With no snapshots, overview returns null rates and is_stale=true."""
    response = await reporting_client.get("/admin/reports/flywheel/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["cohort_fill_avg"] is None
    assert data["open_cohorts_count"] == 0
    assert data["open_cohorts_at_risk_count"] == 0
    assert data["community_to_club_rate"] is None
    assert data["club_to_academy_rate"] is None
    assert data["wallet_cross_service_rate"] is None
    assert data["wallet_active_users"] == 0
    assert data["last_refreshed_at"] is None
    assert data["is_stale"] is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_overview_aggregates_latest_per_metric(reporting_client, db_session):
    """Overview averages cohort fill and surfaces latest funnel + wallet rates."""
    now = datetime.now(timezone.utc)

    # Two cohorts: one full (1.0), one half-full (0.5) — average = 0.75
    cohort_a = CohortFillSnapshot(
        cohort_id=uuid.uuid4(),
        cohort_name="Cohort A",
        capacity=10,
        active_enrollments=10,
        pending_approvals=0,
        waitlist_count=0,
        fill_rate=1.0,
        cohort_status="active",
        days_until_start=None,
        snapshot_taken_at=now,
    )
    cohort_b = CohortFillSnapshot(
        cohort_id=uuid.uuid4(),
        cohort_name="Cohort B",
        capacity=10,
        active_enrollments=5,
        pending_approvals=0,
        waitlist_count=0,
        fill_rate=0.5,
        cohort_status="open",
        days_until_start=10,
        snapshot_taken_at=now,
    )

    funnel_c2c = FunnelConversionSnapshot(
        funnel_stage=FunnelStage.COMMUNITY_TO_CLUB,
        cohort_period="2026-Q1",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        observation_window_days=180,
        source_count=100,
        converted_count=20,
        conversion_rate=0.20,
        snapshot_taken_at=now,
    )
    funnel_c2a = FunnelConversionSnapshot(
        funnel_stage=FunnelStage.CLUB_TO_ACADEMY,
        cohort_period="2026-Q1",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        observation_window_days=270,
        source_count=20,
        converted_count=5,
        conversion_rate=0.25,
        snapshot_taken_at=now,
    )

    wallet = WalletEcosystemSnapshot(
        period_start=date(2026, 2, 1),
        period_end=date(2026, 5, 1),
        period_days=90,
        active_wallet_users=50,
        single_service_users=30,
        cross_service_users=20,
        cross_service_rate=0.40,
        total_bubbles_spent=10_000,
        total_topup_bubbles=15_000,
        snapshot_taken_at=now,
    )

    db_session.add_all([cohort_a, cohort_b, funnel_c2c, funnel_c2a, wallet])
    await db_session.commit()

    response = await reporting_client.get("/admin/reports/flywheel/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["cohort_fill_avg"] == pytest.approx(0.75)
    assert data["open_cohorts_count"] == 2
    # Cohort B is at-risk: <50% fill (0.5 is borderline — strictly "<", so 0.5
    # is NOT counted as at-risk per the AT_RISK_FILL_THRESHOLD logic).
    assert data["open_cohorts_at_risk_count"] == 0
    assert data["community_to_club_rate"] == pytest.approx(0.20)
    assert data["community_to_club_period"] == "2026-Q1"
    assert data["club_to_academy_rate"] == pytest.approx(0.25)
    assert data["club_to_academy_period"] == "2026-Q1"
    assert data["wallet_cross_service_rate"] == pytest.approx(0.40)
    assert data["wallet_active_users"] == 50
    assert data["last_refreshed_at"] is not None
    assert data["is_stale"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_overview_at_risk_cohort_counted(reporting_client, db_session):
    """A cohort with <30% fill within 4 weeks of start counts as at-risk."""
    now = datetime.now(timezone.utc)
    cohort = CohortFillSnapshot(
        cohort_id=uuid.uuid4(),
        cohort_name="At-Risk Cohort",
        capacity=10,
        active_enrollments=2,
        pending_approvals=0,
        waitlist_count=0,
        fill_rate=0.20,
        cohort_status="open",
        days_until_start=14,
        snapshot_taken_at=now,
    )
    db_session.add(cohort)
    await db_session.commit()

    response = await reporting_client.get("/admin/reports/flywheel/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["open_cohorts_count"] == 1
    assert data["open_cohorts_at_risk_count"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_overview_marks_stale_after_36_hours(reporting_client, db_session):
    """A snapshot from 48 hours ago triggers is_stale=True."""
    stale_time = datetime.now(timezone.utc) - timedelta(hours=48)
    wallet = WalletEcosystemSnapshot(
        period_start=date(2026, 2, 1),
        period_end=date(2026, 5, 1),
        period_days=90,
        active_wallet_users=10,
        single_service_users=10,
        cross_service_users=0,
        cross_service_rate=0.0,
        total_bubbles_spent=0,
        total_topup_bubbles=0,
        snapshot_taken_at=stale_time,
    )
    db_session.add(wallet)
    await db_session.commit()

    response = await reporting_client.get("/admin/reports/flywheel/overview")
    assert response.status_code == 200
    assert response.json()["is_stale"] is True


# ---------------------------------------------------------------------------
# /cohorts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cohorts_returns_latest_per_cohort(reporting_client, db_session):
    """When two snapshots exist for one cohort, only the latest is returned."""
    cohort_id = uuid.uuid4()
    older = CohortFillSnapshot(
        cohort_id=cohort_id,
        cohort_name="Cohort X",
        capacity=10,
        active_enrollments=2,
        pending_approvals=0,
        waitlist_count=0,
        fill_rate=0.20,
        cohort_status="open",
        snapshot_taken_at=_utc(2026, 4, 1),
    )
    newer = CohortFillSnapshot(
        cohort_id=cohort_id,
        cohort_name="Cohort X",
        capacity=10,
        active_enrollments=8,
        pending_approvals=0,
        waitlist_count=0,
        fill_rate=0.80,
        cohort_status="open",
        snapshot_taken_at=_utc(2026, 4, 28),
    )
    db_session.add_all([older, newer])
    await db_session.commit()

    response = await reporting_client.get("/admin/reports/flywheel/cohorts")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["fill_rate"] == pytest.approx(0.80)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cohorts_default_sort_is_fill_rate_asc(reporting_client, db_session):
    """Default sort puts the lowest-fill cohort first."""
    now = datetime.now(timezone.utc)
    high = CohortFillSnapshot(
        cohort_id=uuid.uuid4(),
        cohort_name="High",
        capacity=10,
        active_enrollments=9,
        pending_approvals=0,
        waitlist_count=0,
        fill_rate=0.90,
        cohort_status="open",
        snapshot_taken_at=now,
    )
    low = CohortFillSnapshot(
        cohort_id=uuid.uuid4(),
        cohort_name="Low",
        capacity=10,
        active_enrollments=1,
        pending_approvals=0,
        waitlist_count=0,
        fill_rate=0.10,
        cohort_status="open",
        snapshot_taken_at=now,
    )
    db_session.add_all([high, low])
    await db_session.commit()

    response = await reporting_client.get("/admin/reports/flywheel/cohorts")
    assert response.status_code == 200
    rows = response.json()
    assert [r["cohort_name"] for r in rows] == ["Low", "High"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cohorts_status_filter(reporting_client, db_session):
    """status='open' excludes ACTIVE cohorts."""
    now = datetime.now(timezone.utc)
    open_cohort = CohortFillSnapshot(
        cohort_id=uuid.uuid4(),
        cohort_name="Open",
        capacity=10,
        active_enrollments=5,
        pending_approvals=0,
        waitlist_count=0,
        fill_rate=0.5,
        cohort_status="open",
        snapshot_taken_at=now,
    )
    active_cohort = CohortFillSnapshot(
        cohort_id=uuid.uuid4(),
        cohort_name="Active",
        capacity=10,
        active_enrollments=5,
        pending_approvals=0,
        waitlist_count=0,
        fill_rate=0.5,
        cohort_status="active",
        snapshot_taken_at=now,
    )
    db_session.add_all([open_cohort, active_cohort])
    await db_session.commit()

    response = await reporting_client.get(
        "/admin/reports/flywheel/cohorts", params={"status": "open"}
    )
    assert response.status_code == 200
    names = [r["cohort_name"] for r in response.json()]
    assert names == ["Open"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cohorts_invalid_sort_returns_422(reporting_client):
    """Sort regex rejects unknown values."""
    response = await reporting_client.get(
        "/admin/reports/flywheel/cohorts", params={"sort": "bogus"}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# /funnel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_funnel_filters_by_stage(reporting_client, db_session):
    """funnel_stage query filters to a single stage."""
    now = datetime.now(timezone.utc)
    c2c = FunnelConversionSnapshot(
        funnel_stage=FunnelStage.COMMUNITY_TO_CLUB,
        cohort_period="2026-Q1",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        observation_window_days=180,
        source_count=100,
        converted_count=15,
        conversion_rate=0.15,
        snapshot_taken_at=now,
    )
    c2a = FunnelConversionSnapshot(
        funnel_stage=FunnelStage.CLUB_TO_ACADEMY,
        cohort_period="2026-Q1",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        observation_window_days=270,
        source_count=20,
        converted_count=4,
        conversion_rate=0.20,
        snapshot_taken_at=now,
    )
    db_session.add_all([c2c, c2a])
    await db_session.commit()

    response = await reporting_client.get(
        "/admin/reports/flywheel/funnel",
        params={"funnel_stage": "community_to_club"},
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["funnel_stage"] == "community_to_club"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_funnel_filters_by_cohort_period(reporting_client, db_session):
    """cohort_period query filters by string period label."""
    now = datetime.now(timezone.utc)
    q1 = FunnelConversionSnapshot(
        funnel_stage=FunnelStage.COMMUNITY_TO_CLUB,
        cohort_period="2026-Q1",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        observation_window_days=180,
        source_count=100,
        converted_count=15,
        conversion_rate=0.15,
        snapshot_taken_at=now,
    )
    q4 = FunnelConversionSnapshot(
        funnel_stage=FunnelStage.COMMUNITY_TO_CLUB,
        cohort_period="2025-Q4",
        period_start=date(2025, 10, 1),
        period_end=date(2025, 12, 31),
        observation_window_days=180,
        source_count=80,
        converted_count=20,
        conversion_rate=0.25,
        snapshot_taken_at=now,
    )
    db_session.add_all([q1, q4])
    await db_session.commit()

    response = await reporting_client.get(
        "/admin/reports/flywheel/funnel", params={"cohort_period": "2025-Q4"}
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["cohort_period"] == "2025-Q4"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_funnel_limit_clamped(reporting_client):
    """limit=0 is rejected (ge=1)."""
    response = await reporting_client.get(
        "/admin/reports/flywheel/funnel", params={"limit": 0}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# /wallet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_wallet_returns_null_when_no_snapshot(reporting_client):
    response = await reporting_client.get("/admin/reports/flywheel/wallet")
    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_wallet_returns_most_recent(reporting_client, db_session):
    older = WalletEcosystemSnapshot(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 4, 1),
        period_days=90,
        active_wallet_users=10,
        single_service_users=10,
        cross_service_users=0,
        cross_service_rate=0.0,
        total_bubbles_spent=100,
        total_topup_bubbles=200,
        snapshot_taken_at=_utc(2026, 4, 1),
    )
    newer = WalletEcosystemSnapshot(
        period_start=date(2026, 2, 1),
        period_end=date(2026, 5, 1),
        period_days=90,
        active_wallet_users=20,
        single_service_users=15,
        cross_service_users=5,
        cross_service_rate=0.25,
        total_bubbles_spent=500,
        total_topup_bubbles=600,
        snapshot_taken_at=_utc(2026, 4, 28),
    )
    db_session.add_all([older, newer])
    await db_session.commit()

    response = await reporting_client.get("/admin/reports/flywheel/wallet")
    assert response.status_code == 200
    body = response.json()
    assert body["active_wallet_users"] == 20
    assert body["cross_service_rate"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# /refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refresh_enqueues_arq_job(reporting_client):
    """POST /refresh enqueues the ARQ task and returns job_enqueued=True."""
    fake_redis = AsyncMock()
    fake_redis.enqueue_job = AsyncMock()
    fake_redis.close = AsyncMock()

    with patch(
        "services.reporting_service.routers.admin_flywheel.create_pool",
        AsyncMock(return_value=fake_redis),
    ):
        response = await reporting_client.post("/admin/reports/flywheel/refresh")

    assert response.status_code == 200
    body = response.json()
    assert body["job_enqueued"] is True
    fake_redis.enqueue_job.assert_awaited_once_with(
        "task_refresh_all_flywheel", _queue_name="arq:reporting"
    )
    fake_redis.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_refresh_returns_500_on_redis_failure(reporting_client):
    """Failure to acquire the ARQ pool surfaces as a 500."""
    with patch(
        "services.reporting_service.routers.admin_flywheel.create_pool",
        AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        response = await reporting_client.post("/admin/reports/flywheel/refresh")
    assert response.status_code == 500
