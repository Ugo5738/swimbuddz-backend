import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import services.communications_service.tasks.content_publishing as content_publishing
from libs.common.emails.core import EmailDeliveryUnknownError
from services.communications_service.models import ContentPost, ContentPostEmailLog
from services.communications_service.routers.content import _content_post_response
from services.communications_service.tasks.content_publishing import (
    publish_scheduled_content,
    send_content_post_publish_emails,
)
from tests.factories import ContentPostFactory


class Response:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_content_publish_email_is_idempotent(db_session, monkeypatch):
    member_id = "11111111-1111-1111-1111-111111111111"
    image_id = uuid.uuid4()
    post = ContentPostFactory.create(
        is_published=True,
        email_on_publish=True,
        tier_access="community",
        featured_image_media_id=image_id,
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
    resolve_media_url = AsyncMock(
        return_value="https://cdn.example.com/article-image.jpg"
    )
    monkeypatch.setattr(content_publishing, "internal_get", fake_internal_get)
    monkeypatch.setattr(
        content_publishing,
        "resolve_media_url",
        resolve_media_url,
    )
    monkeypatch.setattr(
        content_publishing,
        "send_content_post_published_email",
        send_email,
    )

    assert await send_content_post_publish_emails(db_session, post) == 1
    assert await send_content_post_publish_emails(db_session, post) == 0

    send_email.assert_awaited_once()
    resolve_media_url.assert_awaited_once_with(image_id)
    assert (
        send_email.await_args.kwargs["featured_image_url"]
        == "https://cdn.example.com/article-image.jpg"
    )
    assert send_email.await_args.kwargs["reading_time_minutes"] >= 1
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
    assert logs[0].attempt_count == 1


@pytest.mark.asyncio
async def test_content_publish_email_waits_when_featured_image_cannot_resolve(
    db_session, monkeypatch
):
    post = ContentPostFactory.create(
        is_published=True,
        email_on_publish=True,
        featured_image_media_id=uuid.uuid4(),
    )
    db_session.add(post)
    await db_session.commit()

    monkeypatch.setattr(
        content_publishing,
        "internal_get",
        AsyncMock(
            return_value=Response(
                [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "auth_id": "auth-member",
                        "first_name": "Ada",
                        "email": "ada@example.com",
                    }
                ]
            )
        ),
    )
    monkeypatch.setattr(
        content_publishing,
        "resolve_media_url",
        AsyncMock(return_value=None),
    )
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(
        content_publishing,
        "send_content_post_published_email",
        send_email,
    )

    assert await send_content_post_publish_emails(db_session, post) == 0
    send_email.assert_not_awaited()
    await db_session.refresh(post)
    assert post.email_dispatch_completed_at is None
    assert post.email_dispatch_last_error == (
        "Could not resolve the article featured image"
    )


@pytest.mark.asyncio
async def test_content_publish_email_retries_known_provider_failure(
    db_session, monkeypatch
):
    member_id = "11111111-1111-1111-1111-111111111111"
    post = ContentPostFactory.create(
        is_published=True,
        email_on_publish=True,
        tier_access="community",
    )
    db_session.add(post)
    await db_session.commit()

    monkeypatch.setattr(
        content_publishing,
        "internal_get",
        AsyncMock(
            return_value=Response(
                [
                    {
                        "id": member_id,
                        "auth_id": "auth-member",
                        "first_name": "Ada",
                        "email": "ada@example.com",
                    }
                ]
            )
        ),
    )
    send_email = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(
        content_publishing,
        "send_content_post_published_email",
        send_email,
    )

    assert await send_content_post_publish_emails(db_session, post) == 0
    assert await send_content_post_publish_emails(db_session, post) == 1

    log = (
        await db_session.execute(
            select(ContentPostEmailLog).where(ContentPostEmailLog.post_id == post.id)
        )
    ).scalar_one()
    assert log.delivery_status == "sent"
    assert log.attempt_count == 2


@pytest.mark.asyncio
async def test_content_publish_email_does_not_auto_retry_unknown_outcome(
    db_session, monkeypatch
):
    member_id = "11111111-1111-1111-1111-111111111111"
    post = ContentPostFactory.create(
        is_published=True,
        email_on_publish=True,
        tier_access="community",
    )
    db_session.add(post)
    await db_session.commit()

    monkeypatch.setattr(
        content_publishing,
        "internal_get",
        AsyncMock(
            return_value=Response(
                [
                    {
                        "id": member_id,
                        "auth_id": "auth-member",
                        "first_name": "Ada",
                        "email": "ada@example.com",
                    }
                ]
            )
        ),
    )
    send_email = AsyncMock(side_effect=EmailDeliveryUnknownError("provider timed out"))
    monkeypatch.setattr(
        content_publishing,
        "send_content_post_published_email",
        send_email,
    )

    assert await send_content_post_publish_emails(db_session, post) == 0
    assert await send_content_post_publish_emails(db_session, post) == 0

    send_email.assert_awaited_once()
    log = (
        await db_session.execute(
            select(ContentPostEmailLog).where(ContentPostEmailLog.post_id == post.id)
        )
    ).scalar_one()
    assert log.delivery_status == "unknown"
    assert log.attempt_count == 1


@pytest.mark.asyncio
async def test_content_publish_email_recovers_after_member_lookup_failure(
    db_session, monkeypatch
):
    member_id = "11111111-1111-1111-1111-111111111111"
    post = ContentPostFactory.create(
        is_published=True,
        email_on_publish=True,
        tier_access="community",
    )
    db_session.add(post)
    await db_session.commit()

    member_lookup = AsyncMock(
        side_effect=[
            Response({}, status_code=503),
            Response(
                [
                    {
                        "id": member_id,
                        "auth_id": "auth-member",
                        "first_name": "Ada",
                        "email": "ada@example.com",
                    }
                ]
            ),
        ]
    )
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(content_publishing, "internal_get", member_lookup)
    monkeypatch.setattr(
        content_publishing,
        "send_content_post_published_email",
        send_email,
    )

    assert await send_content_post_publish_emails(db_session, post) == 0
    await db_session.refresh(post)
    assert post.email_recipient_snapshot_at is None
    assert post.email_dispatch_completed_at is None
    assert post.email_dispatch_last_error is not None

    assert await send_content_post_publish_emails(db_session, post) == 1
    await db_session.refresh(post)
    assert post.email_recipient_snapshot_at is not None
    assert post.email_dispatch_completed_at is not None
    assert post.email_dispatch_last_error is None
    send_email.assert_awaited_once()


@pytest.mark.asyncio
async def test_content_response_includes_email_reporting_stats(db_session):
    sent_at = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)
    post = ContentPostFactory.create(is_published=True, email_on_publish=True)
    db_session.add(post)
    await db_session.flush()
    db_session.add_all(
        [
            ContentPostEmailLog(
                post_id=post.id,
                member_id=uuid.uuid4(),
                channel="email",
                delivery_status="sent",
                sent_at=sent_at,
            ),
            ContentPostEmailLog(
                post_id=post.id,
                member_id=uuid.uuid4(),
                channel="email",
                delivery_status="failed",
                error_message="provider error",
            ),
        ]
    )
    await db_session.commit()

    response = await _content_post_response(
        db_session,
        post,
        include_admin_fields=True,
    )

    assert response.email_sent_count == 1
    assert response.email_failed_count == 1
    assert response.email_attempt_count == 0
    assert response.last_email_sent_at == sent_at


@pytest.mark.asyncio
async def test_public_content_response_redacts_editorial_and_delivery_state(db_session):
    post = ContentPostFactory.create(
        is_published=True,
        email_on_publish=True,
        featured_image_prompt="Internal image prompt",
        ai_context_version="swimbuddz-v1",
        email_dispatch_last_error="Provider unavailable",
    )
    db_session.add(post)
    await db_session.commit()

    response = await _content_post_response(db_session, post)

    assert response.featured_image_prompt is None
    assert response.ai_context_version is None
    assert response.email_on_publish is False
    assert response.email_recipient_snapshot_at is None
    assert response.email_dispatch_last_attempt_at is None
    assert response.email_dispatch_completed_at is None
    assert response.email_dispatch_last_error is None


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
