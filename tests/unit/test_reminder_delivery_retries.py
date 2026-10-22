from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from libs.common.datetime_utils import utc_now
from services.communications_service.models import (
    ScheduledNotificationStatus as Status,
    SessionNotificationType as Kind,
)
from services.communications_service.tasks import session_notifications as tasks


@pytest.mark.asyncio
async def test_partial_reminder_failure_retries_only_unsent_member(monkeypatch):
    session_id = uuid4()
    members = [
        {"id": str(uuid4()), "email": f"member{i}@example.com", "first_name": "Member"}
        for i in range(2)
    ]
    session = {
        "id": str(session_id),
        "title": "Club swim",
        "status": "scheduled",
        "starts_at": (utc_now() + timedelta(days=1)).isoformat(),
        "confirmed_booking_member_ids": [m["id"] for m in members],
    }
    notification = NS(
        session_id=session_id,
        notification_type=Kind.REMINDER_24H,
        scheduled_for=utc_now(),
        status=Status.PENDING,
        sent_at=None,
    )
    logs = []

    async def execute(statement):
        values = statement.compile().params
        matching = [
            row
            for row in logs
            if row.member_id == values.get("member_id_1")
            and row.channel == values.get("channel_1")
        ]
        return NS(scalar_one_or_none=lambda: matching[0] if matching else None)

    db = NS(execute=execute, add=logs.append)
    monkeypatch.setattr(tasks, "_get_session_data", AsyncMock(return_value=session))
    monkeypatch.setattr(
        tasks, "_get_session_attendees_and_coaches", AsyncMock(return_value=members)
    )
    monkeypatch.setattr(tasks, "_get_member_preferences", AsyncMock(return_value=None))
    monkeypatch.setattr(
        tasks,
        "build_session_email_contexts",
        AsyncMock(
            return_value=NS(
                sessions={
                    str(session_id): {
                        "id": str(session_id),
                        "title": "Club swim",
                        "date": "Saturday",
                        "time": "10:00",
                        "location": "Yaba",
                    }
                }
            )
        ),
    )
    send = AsyncMock(side_effect=[True, False, True])
    dispatch = AsyncMock(return_value={"dispatched": 2})
    monkeypatch.setattr(tasks, "send_session_reminder_email", send)
    monkeypatch.setattr(tasks, "dispatch_notification", dispatch)
    await tasks._process_single_notification(db, notification)
    assert notification.status == Status.PENDING and notification.sent_at is None
    await tasks._process_single_notification(db, notification)
    assert notification.status == Status.SENT
    assert send.await_count == 3
    assert [call.kwargs["to_email"] for call in send.await_args_list].count(
        members[0]["email"]
    ) == 1
    dispatch.assert_awaited_once()
    assert (
        len(
            [
                row
                for row in logs
                if row.member_id == UUID(members[1]["id"]) and row.channel == "email"
            ]
        )
        == 1
    )
