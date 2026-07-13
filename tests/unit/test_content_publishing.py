from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import services.communications_service.tasks.content_publishing as content_publishing
from services.communications_service.models import ContentPost, ContentPostEmailLog
from services.communications_service.tasks.content_publishing import (
    publish_scheduled_content,
    send_content_post_publish_emails,
)
from tests.factories import ContentPostFactory


class Response:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_content_publish_email_is_idempotent(db_session, monkeypatch):
    member_id = "11111111-1111-1111-1111-111111111111"
    post = ContentPostFactory.create(
        is_published=True,
        email_on_publish=True,
        tier_access="community",
    )
    db_session.add(post)
    await db_session.commit()

    async def fake_internal_get(*, path, **kwargs):
        assert path == "/internal/members/active"
        return Response(
            [
                {
                    "id": member_id,
                    "auth_id": "auth-member",
                    "first_name": "Ada",
                    "email": "ada@example.com",
                    "primary_tier": "community",
                    "active_tiers": ["community"],
                }
            ]
        )

    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(content_publishing, "internal_get", fake_internal_get)
    monkeypatch.setattr(
        content_publishing,
        "send_content_post_published_email",
        send_email,
    )

    assert await send_content_post_publish_emails(db_session, post) == 1
    assert await send_content_post_publish_emails(db_session, post) == 0

    send_email.assert_awaited_once()
    logs = (
        (
            await db_session.execute(
                select(ContentPostEmailLog).where(
                    ContentPostEmailLog.post_id == post.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].member_id.hex == member_id.replace("-", "")
    assert logs[0].delivery_status == "sent"


@pytest.mark.asyncio
async def test_publish_scheduled_content_clears_schedule(db_session, monkeypatch):
    now = datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc)
    post = ContentPostFactory.create(
        is_published=False,
        published_at=None,
        scheduled_for=now - timedelta(minutes=5),
        email_on_publish=False,
    )
    db_session.add(post)
    await db_session.commit()

    original_close = db_session.close
    db_session.close = AsyncMock()

    async def fake_get_async_db():
        yield db_session

    monkeypatch.setattr(content_publishing, "utc_now", lambda: now)
    monkeypatch.setattr(content_publishing, "get_async_db", fake_get_async_db)

    try:
        await publish_scheduled_content()
    finally:
        db_session.close = original_close

    refreshed = await db_session.get(ContentPost, post.id)
    assert refreshed is not None
    assert refreshed.is_published is True
    assert refreshed.published_at == now
    assert refreshed.scheduled_for is None
