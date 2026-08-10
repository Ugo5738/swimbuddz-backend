import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import services.communications_service.routers.email as email_router
import services.wallet_service.services.rewards_engine as rewards_engine
from libs.common.datetime_utils import utc_now
from services.communications_service.models import (
    AnnouncementCategory,
    NotificationPreferences,
)
from services.communications_service.routers.announcements._helpers import (
    _pref_allows_email,
)
from services.communications_service.routers.email import TEMPLATE_EMAIL_PREFERENCE
from services.wallet_service.models.rewards import (
    RewardNotificationPreference,
    WalletEvent,
)


def test_member_email_templates_have_preference_classification():
    assert (
        TEMPLATE_EMAIL_PREFERENCE["enrollment_confirmation"] == "email_academy_updates"
    )
    assert TEMPLATE_EMAIL_PREFERENCE["installment_payment_confirmation"] == (
        "email_payment_receipts"
    )
    assert TEMPLATE_EMAIL_PREFERENCE["coach_assignment"] == "email_coach_messages"
    assert "password_reset" not in TEMPLATE_EMAIL_PREFERENCE


def test_general_announcement_uses_marketing_preference():
    preference = NotificationPreferences(
        member_auth_id="marketing-opt-out",
        email_announcements=True,
        email_marketing=False,
    )

    assert _pref_allows_email(AnnouncementCategory.GENERAL, preference) is False
    assert _pref_allows_email(AnnouncementCategory.EVENT, preference) is True


def test_reward_event_types_select_the_matching_toggle():
    assert rewards_engine._reward_preference_field("referral.qualified") == (
        "notify_on_referral_qualified"
    )


@pytest.mark.asyncio
async def test_member_email_lookup_enforces_exact_recipient_opt_out(monkeypatch):
    preference = NotificationPreferences(
        member_auth_id="academy-opt-out",
        email_academy_updates=False,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = preference
    db = AsyncMock()
    db.execute.return_value = result
    monkeypatch.setattr(
        email_router,
        "search_members",
        AsyncMock(
            return_value=[
                {
                    "auth_id": "academy-opt-out",
                    "email": "Swimmer@Example.com",
                }
            ]
        ),
    )

    allowed = await email_router._member_email_allowed(
        to_email="swimmer@example.com",
        preference_field="email_academy_updates",
        db=db,
    )

    assert allowed is False
    assert rewards_engine._reward_preference_field("referral.milestone") == (
        "notify_on_ambassador_milestone"
    )
    assert rewards_engine._reward_preference_field("attendance.streak") == (
        "notify_on_streak_milestone"
    )
    assert rewards_engine._reward_preference_field("content.published") == (
        "notify_on_reward"
    )


def _wallet_event(*, event_type: str, member_id: uuid.UUID) -> WalletEvent:
    return WalletEvent(
        event_id=uuid.uuid4(),
        event_type=event_type,
        member_auth_id=f"auth-{uuid.uuid4()}",
        member_id=member_id,
        service_source="test",
        occurred_at=utc_now(),
        event_data={},
        idempotency_key=f"test-{uuid.uuid4()}",
    )


@pytest.mark.asyncio
async def test_reward_notification_respects_event_specific_opt_out(monkeypatch):
    member_id = uuid.uuid4()
    event = _wallet_event(event_type="attendance.streak", member_id=member_id)
    preference = RewardNotificationPreference(
        member_auth_id=event.member_auth_id,
        notify_on_reward=True,
        notify_on_referral_qualified=True,
        notify_on_ambassador_milestone=True,
        notify_on_streak_milestone=False,
        notify_channel="in_app",
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = preference
    db = AsyncMock()
    db.execute.return_value = result
    dispatch = AsyncMock()
    monkeypatch.setattr(rewards_engine, "dispatch_notification", dispatch)

    await rewards_engine._dispatch_reward_notification(
        event,
        [{"rule_name": "Four week streak", "bubbles": 40}],
        db,
    )

    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_reward_notification_dispatches_allowed_reward(monkeypatch):
    member_id = uuid.uuid4()
    event = _wallet_event(event_type="content.published", member_id=member_id)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute.return_value = result
    dispatch = AsyncMock(return_value={"dispatched": 1})
    monkeypatch.setattr(rewards_engine, "dispatch_notification", dispatch)

    await rewards_engine._dispatch_reward_notification(
        event,
        [{"rule_name": "Published article", "bubbles": 25}],
        db,
    )

    dispatch.assert_awaited_once()
    assert dispatch.await_args.kwargs["member_ids"] == [str(member_id)]
    assert dispatch.await_args.kwargs["category"] == "rewards"
    assert dispatch.await_args.kwargs["metadata"]["bubbles"] == 25
