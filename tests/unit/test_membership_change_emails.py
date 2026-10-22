from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.members_service.services import membership_notifications as notifications


@pytest.mark.asyncio
async def test_membership_change_sends_email_and_in_app_with_escaped_names(monkeypatch):
    member = SimpleNamespace(email="member@example.com", first_name="Ada <A>")
    db = SimpleNamespace(get=AsyncMock(return_value=member))
    send = AsyncMock(return_value=True)
    dispatch = AsyncMock()
    monkeypatch.setattr(
        notifications, "get_email_client", lambda: SimpleNamespace(send=send)
    )
    monkeypatch.setattr(notifications, "dispatch_notification", dispatch)
    await notifications.notify_membership_change(
        db,
        member_id="member-1",
        title="Your pod has changed",
        body="From Dolphins to Mantas <new>.",
    )
    assert send.await_args.kwargs["to_email"] == member.email
    assert "Mantas &lt;new&gt;" in send.await_args.kwargs["html_body"]
    assert "Ada &lt;A&gt;" in send.await_args.kwargs["html_body"]
    assert dispatch.await_args.kwargs["member_ids"] == ["member-1"]


@pytest.mark.asyncio
async def test_email_failure_does_not_block_in_app_or_committed_change(monkeypatch):
    db = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(email="member@example.com", first_name="Ada")
        )
    )
    monkeypatch.setattr(
        notifications,
        "get_email_client",
        lambda: SimpleNamespace(
            send=AsyncMock(side_effect=RuntimeError("unavailable"))
        ),
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(notifications, "dispatch_notification", dispatch)
    await notifications.notify_membership_change(
        db, member_id="member-1", title="Pod changed", body="New pod"
    )
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_pod_transfer_notifies_once_after_commit(monkeypatch):
    from services.members_service.services import pod_ops
    from services.members_service.models import PodStatus

    source_id, target_id, member_id, club_id = (uuid4() for _ in range(4))
    source = SimpleNamespace(
        id=source_id, club_id=club_id, handle="Dolphins", name="Source"
    )
    target = SimpleNamespace(
        id=target_id,
        club_id=club_id,
        handle="Mantas",
        name="Target",
        status=PodStatus.ACTIVE,
        max_size=5,
    )
    old = SimpleNamespace(left_at=None)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: old)),
        add=lambda _row: None,
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    monkeypatch.setattr(
        pod_ops, "get_pod_or_404", AsyncMock(side_effect=[source, target])
    )
    monkeypatch.setattr(pod_ops, "_active_member_count", AsyncMock(return_value=0))

    async def after_commit(*_args, **kwargs):
        assert db.commit.await_count == 1
        assert kwargs["member_id"] == member_id
        assert "Dolphins" in kwargs["body"] and "Mantas" in kwargs["body"]

    notify = AsyncMock(side_effect=after_commit)
    monkeypatch.setattr(pod_ops, "notify_membership_change", notify)
    await pod_ops.transfer_member(
        db,
        source_pod_id=source_id,
        target_pod_id=target_id,
        member_id=member_id,
        actor_id=uuid4(),
    )
    notify.assert_awaited_once()
