import uuid
from datetime import date
from unittest.mock import AsyncMock

import pytest

from scripts.seed.reward_rules import build_default_rules
from services.volunteer_service.services import monthly_reward


@pytest.mark.asyncio
async def test_manual_and_scheduled_selection_deduplicate_by_member_display_month(
    monkeypatch,
):
    member_id = uuid.uuid4()
    lookup = AsyncMock(return_value={"auth_id": "winner-auth"})
    emit = AsyncMock(return_value={"accepted": True, "rewards_granted": 1})
    monkeypatch.setattr(monthly_reward, "get_member_by_id", lookup)
    monkeypatch.setattr(monthly_reward, "emit_rewards_event", emit)
    assert await monthly_reward.award_volunteer_of_month(member_id, date(2026, 10, 1))
    assert await monthly_reward.award_volunteer_of_month(member_id, date(2026, 10, 20))
    first, repeated = [call.kwargs for call in emit.await_args_list]
    assert first["idempotency_key"] == repeated["idempotency_key"]
    assert first["member_auth_id"] == "winner-auth"
    assert first["event_type"] == "volunteer.monthly_spotlight"
    await monthly_reward.award_volunteer_of_month(member_id, date(2026, 11, 1))
    assert emit.await_args.kwargs["idempotency_key"] != first["idempotency_key"]
    rule = next(
        rule for rule in build_default_rules() if rule.event_type == first["event_type"]
    )
    assert rule.reward_bubbles == 10
    assert not rule.requires_admin_confirmation


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {"accepted": True, "rewards_granted": 0}])
async def test_failed_or_unconfigured_reward_is_not_reported_as_paid(
    monkeypatch, result
):
    monkeypatch.setattr(
        monthly_reward,
        "get_member_by_id",
        AsyncMock(return_value={"auth_id": "winner"}),
    )
    monkeypatch.setattr(
        monthly_reward, "emit_rewards_event", AsyncMock(return_value=result)
    )
    assert not await monthly_reward.award_volunteer_of_month(
        uuid.uuid4(), date(2026, 10, 1)
    )
