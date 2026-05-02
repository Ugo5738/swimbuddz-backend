"""Integration tests for flywheel snapshot computation tasks.

These exercise ``services.reporting_service.tasks.flywheel`` end-to-end:
the task fetches data via ``internal_get`` (mocked), runs the math, and
persists snapshot rows. Persistence is verified directly by querying the
test session.

The tasks themselves use ``AsyncSessionLocal`` (a real Postgres session),
not the test ``db_session``. To avoid that we patch the session factory
inside each task to yield the test session.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from services.reporting_service.models import (
    CohortFillSnapshot,
    FunnelConversionSnapshot,
    FunnelStage,
    WalletEcosystemSnapshot,
)


@pytest_asyncio.fixture(autouse=True)
async def _clean_flywheel_tables(db_session):
    """Clear pre-existing flywheel rows; rollback at end restores them."""
    await db_session.execute(delete(CohortFillSnapshot))
    await db_session.execute(delete(FunnelConversionSnapshot))
    await db_session.execute(delete(WalletEcosystemSnapshot))
    await db_session.flush()
    yield


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _mock_resp(payload: dict, status: int = 200):
    """Build a MagicMock response that matches the ``internal_get`` shape used by the task."""
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=payload)
    resp.raise_for_status = MagicMock(return_value=None)
    return resp


def _patch_session_factory(db_session):
    """Patch ``AsyncSessionLocal`` to yield the test session.

    The flywheel tasks open their own session via ``async with
    AsyncSessionLocal() as db: ...``. We replace that with a context
    manager that yields ``db_session`` and skips its ``commit/close``
    so the test transaction stays in control.
    """

    @asynccontextmanager
    async def _factory():
        yield db_session

    # Replace ``AsyncSessionLocal()`` (a callable returning an async ctx mgr)
    # with our factory function.
    return patch(
        "services.reporting_service.tasks.flywheel.AsyncSessionLocal",
        _factory,
    )


def _route_internal_get(routes: dict[str, Callable[[dict | None], dict]]):
    """Build an ``internal_get`` AsyncMock that dispatches by path.

    ``routes`` maps a path substring to a callable taking the params dict
    and returning the JSON payload.
    """

    async def _fake(
        *, service_url: str, path: str, calling_service: str, params=None, **_
    ):
        for needle, builder in routes.items():
            if needle in path:
                return _mock_resp(builder(params or {}))
        raise AssertionError(f"unexpected internal_get path: {path}")

    return AsyncMock(side_effect=_fake)


# ---------------------------------------------------------------------------
# compute_cohort_fill_snapshots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cohort_fill_snapshot_computes_fill_rate(db_session):
    """Active+pending / capacity is the fill rate; days_until_start is computed."""
    from services.reporting_service.tasks import flywheel as flywheel_tasks

    cohort_id = str(uuid.uuid4())
    starts_at = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()

    cohorts_payload = {
        "cohorts": [
            {
                "id": cohort_id,
                "name": "Test Cohort",
                "program_name": "Adult Beginner",
                "capacity": 10,
                "status": "OPEN",
                "start_date": starts_at,
                "end_date": None,
            }
        ]
    }
    counts_payload = {"active": 6, "pending_approval": 2, "waitlist": 1}

    fake_get = _route_internal_get(
        {
            f"/internal/academy/cohorts/{cohort_id}/enrollment-counts": lambda _p: counts_payload,
            "/internal/academy/cohorts": lambda _p: cohorts_payload,
        }
    )

    with (
        _patch_session_factory(db_session),
        patch("services.reporting_service.tasks.flywheel.internal_get", fake_get),
    ):
        count = await flywheel_tasks.compute_cohort_fill_snapshots()

    assert count == 1
    row = (await db_session.execute(select(CohortFillSnapshot))).scalar_one()
    assert row.capacity == 10
    assert row.active_enrollments == 6
    assert row.pending_approvals == 2
    assert row.waitlist_count == 1
    # (active + pending) / capacity = 8 / 10
    assert row.fill_rate == pytest.approx(0.80)
    assert row.cohort_status == "open"
    assert row.days_until_start is not None
    assert 9 <= row.days_until_start <= 11


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cohort_fill_snapshot_zero_capacity_safe(db_session):
    """A cohort with capacity=0 doesn't divide by zero."""
    from services.reporting_service.tasks import flywheel as flywheel_tasks

    cohort_id = str(uuid.uuid4())
    cohorts = {
        "cohorts": [
            {
                "id": cohort_id,
                "name": "Empty",
                "capacity": 0,
                "status": "open",
                "start_date": None,
            }
        ]
    }
    fake_get = _route_internal_get(
        {
            f"/internal/academy/cohorts/{cohort_id}/enrollment-counts": lambda _p: {
                "active": 0,
                "pending_approval": 0,
                "waitlist": 0,
            },
            "/internal/academy/cohorts": lambda _p: cohorts,
        }
    )

    with (
        _patch_session_factory(db_session),
        patch("services.reporting_service.tasks.flywheel.internal_get", fake_get),
    ):
        await flywheel_tasks.compute_cohort_fill_snapshots()

    row = (await db_session.execute(select(CohortFillSnapshot))).scalar_one()
    assert row.fill_rate == 0.0
    assert row.days_until_start is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cohort_fill_snapshot_no_cohorts_returns_zero(db_session):
    from services.reporting_service.tasks import flywheel as flywheel_tasks

    fake_get = _route_internal_get(
        {"/internal/academy/cohorts": lambda _p: {"cohorts": []}}
    )

    with (
        _patch_session_factory(db_session),
        patch("services.reporting_service.tasks.flywheel.internal_get", fake_get),
    ):
        result = await flywheel_tasks.compute_cohort_fill_snapshots()

    assert result == 0
    count = (
        await db_session.execute(select(func.count()).select_from(CohortFillSnapshot))
    ).scalar_one()
    assert count == 0


# ---------------------------------------------------------------------------
# compute_funnel_conversions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_funnel_conversion_persists_three_stages(db_session):
    """A successful run writes one snapshot per stage (3 total)."""
    from services.reporting_service.tasks import flywheel as flywheel_tasks

    # Empty membership/tier history: all three funnels resolve to 0/0.
    def joined_tier(_params):
        return {"members": []}

    def tier_history(_params):
        return {"entries": []}

    fake_get = _route_internal_get(
        {
            "/internal/members/joined-tier": joined_tier,
            "/tier-history": tier_history,
        }
    )

    with (
        _patch_session_factory(db_session),
        patch("services.reporting_service.tasks.flywheel.internal_get", fake_get),
    ):
        count = await flywheel_tasks.compute_funnel_conversions(period_label="2026-Q1")

    assert count == 3
    rows = (await db_session.execute(select(FunnelConversionSnapshot))).scalars().all()
    assert {r.funnel_stage for r in rows} == {
        FunnelStage.COMMUNITY_TO_CLUB,
        FunnelStage.CLUB_TO_ACADEMY,
        FunnelStage.COMMUNITY_TO_ACADEMY,
    }
    for row in rows:
        assert row.cohort_period == "2026-Q1"
        assert row.period_start == date(2026, 1, 1)
        assert row.period_end == date(2026, 3, 31)
        assert row.source_count == 0
        assert row.converted_count == 0
        assert row.conversion_rate == 0.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_funnel_conversion_counts_crossover(db_session):
    """A member who crosses to the target tier within window counts as converted."""
    from services.reporting_service.tasks import flywheel as flywheel_tasks

    member_id = str(uuid.uuid4())
    joined_at = "2026-02-01T10:00:00+00:00"

    def joined_tier(params):
        if params.get("tier") == "community":
            return {
                "members": [
                    {
                        "id": member_id,
                        "source_joined_at": joined_at,
                        "acquisition_source": "social_instagram",
                    }
                ]
            }
        return {"members": []}

    def tier_history(_params):
        # community→club crossover at 2026-03-15 (within 180-day window from joined_at)
        return {
            "entries": [
                {"tier": "community", "entered_at": joined_at, "exited_at": None},
                {
                    "tier": "club",
                    "entered_at": "2026-03-15T10:00:00+00:00",
                    "exited_at": None,
                },
            ]
        }

    fake_get = _route_internal_get(
        {
            "/internal/members/joined-tier": joined_tier,
            "/tier-history": tier_history,
        }
    )

    with (
        _patch_session_factory(db_session),
        patch("services.reporting_service.tasks.flywheel.internal_get", fake_get),
    ):
        await flywheel_tasks.compute_funnel_conversions(period_label="2026-Q1")

    c2c = (
        await db_session.execute(
            select(FunnelConversionSnapshot).where(
                FunnelConversionSnapshot.funnel_stage == FunnelStage.COMMUNITY_TO_CLUB
            )
        )
    ).scalar_one()
    assert c2c.source_count == 1
    assert c2c.converted_count == 1
    assert c2c.conversion_rate == pytest.approx(1.0)
    assert c2c.breakdown_by_source == {"social_instagram": 1}


# ---------------------------------------------------------------------------
# compute_wallet_ecosystem_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_wallet_ecosystem_snapshot_basic(db_session):
    """Cross-service rate = cross / active; missing fields default sensibly."""
    from services.reporting_service.tasks import flywheel as flywheel_tasks

    fake_get = _route_internal_get(
        {
            "/internal/wallet/ecosystem-stats": lambda _p: {
                "active_wallet_users": 100,
                "single_service_users": 70,
                "cross_service_users": 30,
                "total_bubbles_spent": 5000,
                "total_topup_bubbles": 7000,
                "spend_distribution": {"sessions": 0.5, "academy": 0.3, "store": 0.2},
            }
        }
    )

    with (
        _patch_session_factory(db_session),
        patch("services.reporting_service.tasks.flywheel.internal_get", fake_get),
    ):
        snap = await flywheel_tasks.compute_wallet_ecosystem_snapshot(window_days=30)

    assert snap.active_wallet_users == 100
    assert snap.cross_service_users == 30
    assert snap.cross_service_rate == pytest.approx(0.30)
    assert snap.period_days == 30
    assert (snap.period_end - snap.period_start) == timedelta(days=30)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_wallet_ecosystem_snapshot_zero_active_safe(db_session):
    """Zero active users -> rate=0, not divide-by-zero."""
    from services.reporting_service.tasks import flywheel as flywheel_tasks

    fake_get = _route_internal_get(
        {
            "/internal/wallet/ecosystem-stats": lambda _p: {
                "active_wallet_users": 0,
                "single_service_users": 0,
                "cross_service_users": 0,
                "total_bubbles_spent": 0,
                "total_topup_bubbles": 0,
            }
        }
    )

    with (
        _patch_session_factory(db_session),
        patch("services.reporting_service.tasks.flywheel.internal_get", fake_get),
    ):
        snap = await flywheel_tasks.compute_wallet_ecosystem_snapshot()

    assert snap.cross_service_rate == 0.0
