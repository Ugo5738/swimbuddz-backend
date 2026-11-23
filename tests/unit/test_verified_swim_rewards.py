import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.attendance_service.routers.internal import compute_milestone_metrics
from services.attendance_service.services import swim_rewards as attendance
from services.wallet_service.routers.rewards_member import _clean_reversal_description
from services.wallet_service.services import swim_rewards as wallet_swim_rewards
from services.wallet_service.services.swim_rewards import (
    _format_swim_credit_description,
    _format_swim_reversal_description,
    member_progress_with_milestones,
    progress,
)


def test_swim_days_week_caps_streak_and_correction():
    today = date(2026, 11, 20)
    dates = [2, 9, 16, 16, 17, 18, 19, 20]
    rows = [SimpleNamespace(swim_date=date(2026, 11, day), issued=0) for day in dates]
    rows.append(SimpleNamespace(swim_date=None, issued=6))
    result = progress(rows, today)
    assert (result.total_swims, result.active_weeks, result.streak_weeks) == (8, 3, 3)
    assert (result.week_days, result.bubbles_earned, result.bubbles_credited) == (
        3,
        5,
        6,
    )
    assert result.today_counted and result.reward_value_naira == 500
    assert result.all_time_swims == 8
    assert result.all_time_best_streak == 3
    assert result.all_time_available is False
    assert progress(rows, date(2026, 11, 23)).streak_weeks == 3
    assert progress(rows, date(2026, 11, 30)).streak_weeks == 0


def test_all_time_milestone_metrics_handle_gaps_best_streak_and_lagos_tz():
    # 4-week streak in Oct (weeks of Oct 5, 12, 19, 26), gap week Nov 2, then 2 swims in Nov 16 week
    starts = [
        datetime(2026, 10, 4, 23, 30, tzinfo=timezone.utc),  # Oct 5 in Lagos (Mon)
        datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 19, 9, 0, tzinfo=timezone.utc),
    ]
    metrics = compute_milestone_metrics(starts, date(2026, 11, 23))
    assert metrics.attendance_available is True
    assert metrics.total_attended_swims == 6
    assert metrics.active_weeks == 5
    assert metrics.best_streak_weeks == 4
    assert metrics.current_streak_weeks == 1


@pytest.mark.asyncio
async def test_member_progress_with_milestones_enriches_and_falls_back(monkeypatch):
    base = progress([], date(2026, 11, 23))
    monkeypatch.setattr(
        wallet_swim_rewards, "member_progress", AsyncMock(return_value=base)
    )
    monkeypatch.setattr(
        wallet_swim_rewards,
        "fetch_all_time_milestones",
        AsyncMock(
            return_value={
                "total_attended_swims": 8,
                "active_weeks": 6,
                "best_streak_weeks": 4,
                "current_streak_weeks": 1,
                "attendance_available": True,
            }
        ),
    )
    enriched = await member_progress_with_milestones(None, "auth-1")
    assert enriched.bubbles_earned == 0
    assert enriched.total_swims == 0
    assert enriched.all_time_swims == 8
    assert enriched.all_time_active_weeks == 6
    assert enriched.all_time_best_streak == 4
    assert enriched.all_time_current_streak == 1
    assert enriched.all_time_available is True

    monkeypatch.setattr(
        wallet_swim_rewards, "fetch_all_time_milestones", AsyncMock(return_value=None)
    )
    fallback = await member_progress_with_milestones(None, "auth-1")
    assert fallback.all_time_swims == 0
    assert fallback.all_time_available is False


def test_human_readable_swim_descriptions_never_expose_uuids():
    sid = uuid.uuid4()
    credit = _format_swim_credit_description(
        date(2026, 11, 24), "SwimBuddz Yaba Club practice"
    )
    assert credit == "Swim Bubble · SwimBuddz Yaba Club practice (2026-11-24)"

    rev = _format_swim_reversal_description(
        date(2026, 11, 24), "SwimBuddz Yaba Club practice"
    )
    assert rev == "Swim Bubble reversed · SwimBuddz Yaba Club practice (2026-11-24)"
    assert str(sid) not in rev

    legacy_txn = SimpleNamespace(
        description=f"Swim reward reversal · {sid}",
        txn_metadata={"session_title": "Yaba Club practice", "swim_date": "2026-11-24"},
    )
    assert (
        _clean_reversal_description(legacy_txn)
        == "Swim Bubble reversed · Yaba Club practice (2026-11-24)"
    )

    legacy_bare = SimpleNamespace(
        description=f"Swim reward reversal · {sid}", txn_metadata={}
    )
    assert (
        _clean_reversal_description(legacy_bare)
        == "Swim Bubble reversed · Attendance correction"
    )


@pytest.mark.asyncio
async def test_reward_history_includes_net_lifetime_total_and_reversals():
    from services.wallet_service.routers.rewards_member import get_my_reward_history

    t1 = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 25, 11, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)

    history_row = (
        SimpleNamespace(id=uuid.uuid4(), bubbles_awarded=15, created_at=t1),
        SimpleNamespace(
            rule_name="referral_qualified",
            display_name="Referral Reward",
            category=SimpleNamespace(value="acquisition"),
            description="Referral bonus",
        ),
        "Referral reward — Chidi joined",
    )
    swim_row = (
        SimpleNamespace(id=uuid.uuid4(), bubbles_awarded=1, created_at=t2),
        SimpleNamespace(
            rule_name="verified_swim_day",
            display_name="Daily Swim Reward",
            category=SimpleNamespace(value="retention"),
            description="Daily swim",
        ),
        "Swim Bubble · Yaba Club practice (2026-11-24)",
    )
    reversal_txn = SimpleNamespace(
        id=uuid.uuid4(),
        amount=1,
        description=f"Swim reward reversal · {uuid.uuid4()}",
        txn_metadata={
            "session_title": "Yaba Club practice",
            "swim_date": "2026-11-24",
        },
        created_at=t3,
    )

    class FakeResult:
        def __init__(self, rows=None, one_row=None):
            self._rows = rows or []
            self._one = one_row

        def all(self):
            return self._rows

        def one(self):
            return self._one

    class FakeDB:
        def __init__(self):
            self.exec_calls = 0

        async def execute(self, _stmt):
            self.exec_calls += 1
            if self.exec_calls == 1:
                return FakeResult(rows=[swim_row, history_row])
            if self.exec_calls == 2:
                return FakeResult(one_row=SimpleNamespace(row_count=12, bubbles_sum=25))
            return FakeResult(one_row=SimpleNamespace(row_count=1, reversed_sum=1))

        async def scalars(self, _stmt):
            return FakeResult(rows=[reversal_txn])

    user = SimpleNamespace(user_id="auth-member-1")
    res = await get_my_reward_history(
        limit=10, skip=0, offset=None, category=None, current_user=user, db=FakeDB()
    )
    # Lifetime totals reflect all 12+1 rows (not just the page slice) and net out reversals
    assert res["total"] == 13
    assert res["total_bubbles_awarded"] == 24
    assert len(res["history"]) == 3
    assert res["history"][0]["is_reversal"] is True
    assert res["history"][0]["bubbles_awarded"] == -1
    assert (
        res["history"][0]["description"]
        == "Swim Bubble reversed · Yaba Club practice (2026-11-24)"
    )
    assert res["history"][1]["bubbles_awarded"] == 1
    assert res["history"][2]["bubbles_awarded"] == 15


@pytest.mark.asyncio
async def test_local_day_and_event_eligibility(monkeypatch):
    session = {
        "session_type": "club",
        "status": "completed",
        "starts_at": "2026-10-04T23:30:00Z",
        "ends_at": "2026-10-05T01:30:00Z",
    }
    assert await attendance.eligible_swim_date(session) == date(2026, 10, 5)
    for status in ["draft", "cancelled"]:
        assert (
            await attendance.eligible_swim_date({**session, "status": status}) is None
        )
    assert (
        await attendance.eligible_swim_date(
            {
                **session,
                "starts_at": "2099-11-20T10:00:00Z",
                "ends_at": "2099-11-20T11:00:00Z",
            }
        )
        is None
    )
    event = {**session, "session_type": "event", "event_id": str(uuid.uuid4())}
    contract = AsyncMock(return_value={"event_type": "social", "status": "published"})
    monkeypatch.setattr(attendance, "get_event_session_contract", contract)
    assert await attendance.eligible_swim_date(event) is None
    contract.return_value = {"event_type": "community_swim", "status": "published"}
    assert await attendance.eligible_swim_date(event) == date(2026, 10, 5)
