from datetime import time, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from libs.common.datetime_utils import utc_now
from services.members_service.models import PodStatus
from services.members_service.services import pod_ops, assessment_notifications
from services.sessions_service.routers import club_operations


@pytest.mark.asyncio
async def test_pod_schedule_change_notifies_roster_before_commit(monkeypatch):
    member_id = uuid4()
    pod = NS(
        id=uuid4(),
        status=PodStatus.ACTIVE,
        name="Dolphins",
        handle=None,
        pod_lead_id=uuid4(),
        assistant_pod_lead_id=None,
        default_session_day="saturday",
        default_session_time=time(10),
        default_session_duration_minutes=60,
    )
    result = NS(scalars=lambda: NS(all=lambda: [member_id]))
    db = NS(
        execute=AsyncMock(return_value=result), commit=AsyncMock(), refresh=AsyncMock()
    )
    monkeypatch.setattr(pod_ops, "get_pod_or_404", AsyncMock(return_value=pod))

    async def queued(*_args, **kwargs):
        assert db.commit.await_count == 0
        assert kwargs["member_id"] == member_id
        assert "11:30" in kwargs["body"]

    notify = AsyncMock(side_effect=queued)
    monkeypatch.setattr(pod_ops, "notify_membership_change", notify)
    await pod_ops.update_pod(
        db, pod_id=pod.id, fields={"default_session_time": time(11, 30)}
    )
    notify.assert_awaited_once()
    db.commit.assert_awaited_once()
    notify.reset_mock()
    await pod_ops.update_pod(
        db, pod_id=pod.id, fields={"default_session_time": time(11, 30)}
    )
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_assessment_acknowledges_member_and_alerts_reviewer_idempotently(
    monkeypatch,
):
    member = NS(id=uuid4(), first_name="Ada", last_name="Okafor")
    application = NS(id=uuid4(), club_id=uuid4())
    db = NS(get=AsyncMock(return_value=NS(name="Yaba Club")))
    ack, enqueue = AsyncMock(), AsyncMock()
    monkeypatch.setattr(assessment_notifications, "notify_membership_change", ack)
    monkeypatch.setattr(assessment_notifications, "enqueue_operational_email", enqueue)
    monkeypatch.setattr(
        assessment_notifications.get_settings(),
        "CLUB_REVIEW_NOTIFICATION_EMAILS",
        ["ops@example.com"],
    )
    for _ in range(2):
        await assessment_notifications.notify_assessment_submission(
            db,
            member=member,
            application=application,
            stage="video",
            submission={"video": "one"},
        )
    assert ack.await_args.kwargs["member_id"] == member.id
    assert enqueue.await_args.kwargs["recipient_email"] == "ops@example.com"
    assert (
        ack.await_args_list[0].kwargs["event_key"]
        == ack.await_args_list[1].kwargs["event_key"]
    )
    assert (
        enqueue.await_args_list[0].kwargs["event_key"]
        == enqueue.await_args_list[1].kwargs["event_key"]
    )


@pytest.mark.asyncio
async def test_reschedule_queues_member_and_guest_emails_in_one_transaction(
    monkeypatch,
):
    member_id = uuid4()
    session = NS(id=uuid4(), title="Club swim", starts_at=utc_now() + timedelta(days=2))
    operation = NS(
        id=uuid4(),
        before=[{"starts_at": (session.starts_at - timedelta(days=1)).isoformat()}],
        after=[{"starts_at": session.starts_at.isoformat()}],
        payload={"reason": "Pool change"},
        notification_status="pending",
    )
    results = [
        NS(scalars=lambda: NS(all=lambda: [member_id, member_id])),
        NS(scalars=lambda: NS(all=lambda: ["Guest@example.com", "guest@example.com"])),
    ]
    db = NS(execute=AsyncMock(side_effect=results), commit=AsyncMock())
    enqueue = AsyncMock()
    monkeypatch.setattr(club_operations, "enqueue_operational_email", enqueue)
    await club_operations.notify_reschedule(db, operation, session)
    assert operation.notification_status == "queued"
    assert enqueue.await_count == 2
    assert enqueue.await_args_list[0].kwargs["member_id"] == member_id
    assert enqueue.await_args_list[1].kwargs["recipient_email"] == "guest@example.com"
    assert all(
        call.kwargs["expires_at"] == session.starts_at
        for call in enqueue.await_args_list
    )
    db.commit.assert_awaited_once()
