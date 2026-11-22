import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.attendance_service.services import swim_rewards as attendance
from services.wallet_service.services.swim_rewards import progress


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
    assert progress(rows, date(2026, 11, 23)).streak_weeks == 3
    assert progress(rows, date(2026, 11, 30)).streak_weeks == 0


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
