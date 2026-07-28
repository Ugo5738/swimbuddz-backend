from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

import services.communications_service.tasks.session_notifications as notifications
from services.communications_service.models import (
    ScheduledNotificationStatus,
    SessionNotificationType,
)
from services.communications_service.services.session_email_context import (
    SessionEmailContextBatch,
)

MEMBER_ID = "11111111-1111-1111-1111-111111111111"
SESSION_ID = "22222222-2222-2222-2222-222222222222"


def _context() -> dict:
    return {
        "id": SESSION_ID,
        "title": "Orca Technique",
        "session_type": "club",
        "date": "Saturday, August 01, 2026",
        "time": "09:00 AM",
        "location": "Rowe Park Pool",
        "calendar_url": "https://calendar.test/event",
    }


@pytest.mark.asyncio
async def test_daily_prompt_passes_shared_context_to_template(monkeypatch):
    db = SimpleNamespace(add=lambda _entry: None)
    member = {
        "id": MEMBER_ID,
        "auth_id": "auth-member",
        "email": "member@example.com",
        "first_name": "Member",
    }
    session = {
        "id": SESSION_ID,
        "title": "Orca Technique",
        "session_type": "club",
        "starts_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
    }
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(
        notifications,
        "_get_session_announcement_members",
        AsyncMock(return_value=[member]),
    )
    monkeypatch.setattr(
        notifications,
        "get_confirmed_booking_member_ids",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        notifications,
        "_get_notification_preferences_by_auth",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        notifications,
        "_sent_session_notification_recently",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(notifications, "_has_paid_session_access", lambda *_: True)
    monkeypatch.setattr(notifications, "send_session_announcement_email", send_email)
    monkeypatch.setattr(
        notifications,
        "dispatch_notification",
        AsyncMock(return_value=None),
    )

    sent = await notifications._send_booking_prompt_for_session(
        db,
        session=session,
        active_members=[member],
        email_context=_context(),
        is_follow_up=True,
    )

    assert sent == 1
    sent_context = send_email.await_args.kwargs["session"]
    assert sent_context["id"] == SESSION_ID
    assert sent_context["is_booked"] is False
    assert sent_context["state_label"] == "Available for you to book"
    assert send_email.await_args.kwargs["is_follow_up"] is True


@pytest.mark.asyncio
async def test_timed_reminder_passes_shared_context_to_template(monkeypatch):
    session = {
        "id": SESSION_ID,
        "title": "Orca Technique",
        "session_type": "club",
        "status": "scheduled",
        "starts_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "confirmed_booking_member_ids": [MEMBER_ID],
    }
    member = {
        "id": MEMBER_ID,
        "auth_id": "auth-member",
        "email": "member@example.com",
        "first_name": "Member",
    }
    notification = SimpleNamespace(
        id=UUID("33333333-3333-3333-3333-333333333333"),
        session_id=UUID(SESSION_ID),
        notification_type=SessionNotificationType.REMINDER_24H,
        status=ScheduledNotificationStatus.PENDING,
        sent_at=None,
        error_message=None,
    )
    empty_result = SimpleNamespace(scalar_one_or_none=lambda: None)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=empty_result),
        add=lambda _entry: None,
    )
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(
        notifications,
        "_get_session_data",
        AsyncMock(return_value=session),
    )
    monkeypatch.setattr(
        notifications,
        "_get_session_attendees_and_coaches",
        AsyncMock(return_value=[member]),
    )
    monkeypatch.setattr(
        notifications,
        "_get_member_preferences",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        notifications,
        "build_session_email_contexts",
        AsyncMock(
            return_value=SessionEmailContextBatch(
                sessions={SESSION_ID: _context()},
                audience_configs={},
                pods_by_session={},
                pod_member_ids_by_session={},
            )
        ),
    )
    monkeypatch.setattr(notifications, "send_session_reminder_email", send_email)
    monkeypatch.setattr(
        notifications,
        "dispatch_notification",
        AsyncMock(return_value=None),
    )

    await notifications._process_single_notification(db, notification)

    sent_context = send_email.await_args.kwargs["session"]
    assert sent_context["id"] == SESSION_ID
    assert sent_context["is_booked"] is True
    assert sent_context["state_label"] == "You are booked"
    assert notification.status == ScheduledNotificationStatus.SENT
