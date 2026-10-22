from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, func

from libs.common.datetime_utils import utc_now
from libs.common.operational_email import OperationalEmail, enqueue_operational_email
from services.communications_service.models import Notification
from services.communications_service.tasks import operational_emails as delivery


@pytest.mark.asyncio
async def test_operational_email_retries_without_duplicate_notification(
    db_session, monkeypatch
):
    member_id = uuid4()
    await enqueue_operational_email(
        db_session,
        event_key="pod:retry-one",
        member_id=member_id,
        recipient_email="member@example.com",
        subject="Pod changed",
        body="Your pod has changed.",
    )
    await db_session.commit()
    send = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(delivery, "send_email", send)
    assert await delivery.deliver_operational_emails(db_session) == {
        "sent": 0,
        "failed": 1,
        "expired": 0,
    }
    row = (
        await db_session.execute(
            select(OperationalEmail).where(
                OperationalEmail.event_key == "pod:retry-one"
            )
        )
    ).scalar_one()
    assert row.attempt_count == 1
    assert row.status == "failed" and row.sent_at is None
    row.next_attempt_at = utc_now() - timedelta(seconds=1)
    await db_session.commit()
    assert await delivery.deliver_operational_emails(db_session) == {
        "sent": 1,
        "failed": 0,
        "expired": 0,
    }
    assert row.status == "sent" and row.attempt_count == 2
    assert (
        await db_session.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.id == row.id)
        )
    ).scalar_one() == 1
    assert await delivery.deliver_operational_emails(db_session) == {
        "sent": 0,
        "failed": 0,
        "expired": 0,
    }
    assert send.await_count == 2


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_and_rollback_preserves_atomicity(db_session):
    kwargs = dict(
        event_key="event:dedupe",
        recipient_email="member@example.com",
        subject="Update",
        body="Changed",
    )
    await enqueue_operational_email(db_session, **kwargs)
    await enqueue_operational_email(db_session, **kwargs)
    assert (
        await db_session.execute(
            select(func.count())
            .select_from(OperationalEmail)
            .where(OperationalEmail.event_key == kwargs["event_key"])
        )
    ).scalar_one() == 1
    transaction = await db_session.begin_nested()
    await enqueue_operational_email(
        db_session, **{**kwargs, "event_key": "event:rolled-back"}
    )
    await transaction.rollback()
    assert (
        await db_session.execute(
            select(func.count())
            .select_from(OperationalEmail)
            .where(OperationalEmail.event_key == "event:rolled-back")
        )
    ).scalar_one() == 0


@pytest.mark.asyncio
async def test_expired_schedule_email_is_not_sent(db_session, monkeypatch):
    await enqueue_operational_email(
        db_session,
        event_key="expired:one",
        member_id=uuid4(),
        recipient_email="member@example.com",
        subject="Reschedule",
        body="Old change",
        expires_at=utc_now() - timedelta(seconds=1),
    )
    await db_session.commit()
    send = AsyncMock()
    monkeypatch.setattr(delivery, "send_email", send)
    assert await delivery.deliver_operational_emails(db_session) == {
        "sent": 0,
        "failed": 0,
        "expired": 1,
    }
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_unresolved_member_email_retries_then_resolves(db_session, monkeypatch):
    member_id = uuid4()
    await enqueue_operational_email(
        db_session,
        event_key="reschedule:lookup",
        member_id=member_id,
        subject="Rescheduled",
        body="New date",
    )
    await db_session.commit()
    lookup = AsyncMock(
        side_effect=[[], [{"id": str(member_id), "email": "resolved@example.com"}]]
    )
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(delivery, "get_members_bulk", lookup)
    monkeypatch.setattr(delivery, "send_email", send)
    assert (await delivery.deliver_operational_emails(db_session))["failed"] == 1
    row = (
        await db_session.execute(
            select(OperationalEmail).where(
                OperationalEmail.event_key == "reschedule:lookup"
            )
        )
    ).scalar_one()
    row.next_attempt_at = utc_now() - timedelta(seconds=1)
    await db_session.commit()
    assert (await delivery.deliver_operational_emails(db_session))["sent"] == 1
    assert send.await_args.kwargs["to_email"] == "resolved@example.com"
