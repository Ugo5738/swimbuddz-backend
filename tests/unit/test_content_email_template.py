from unittest.mock import AsyncMock

import pytest

import services.communications_service.templates.content as content_template


def test_reading_time_uses_visible_blocknote_text_only():
    body = (
        '[{"id":"not-reader-text","type":"paragraph","props":'
        '{"textColor":"default"},"content":['
        '{"type":"text","text":"' + "word " * 226 + '","styles":{}}]}]'
    )

    assert content_template.estimate_article_reading_time(body) == 2


def test_reading_time_supports_markdown_and_has_one_minute_floor():
    assert content_template.estimate_article_reading_time("# Quick swim tip") == 1
    assert content_template.estimate_article_reading_time("") == 1


@pytest.mark.asyncio
async def test_published_article_email_renders_featured_image(monkeypatch):
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(content_template, "send_email", send_email)

    result = await content_template.send_content_post_published_email(
        to_email="ada@example.com",
        member_name="Ada",
        post_id="article-id",
        title='Breathing & "Balance"',
        summary="A practical guide.",
        category="swimming_tips",
        featured_image_url="https://cdn.example.com/article.jpg?size=large&fit=cover",
        reading_time_minutes=4,
    )

    assert result is True
    send_email.assert_awaited_once()
    html_body = send_email.await_args.args[3]
    assert (
        'src="https://cdn.example.com/article.jpg?size=large&amp;fit=cover"'
        in html_body
    )
    assert 'alt="Breathing &amp; &quot;Balance&quot; featured image"' in html_body
    assert "width:100%" in html_body
    assert "4 min read" in html_body
    plain_body = send_email.await_args.args[2]
    assert "Estimated reading time: 4 min" in plain_body
    assert "/tips/article-id" in plain_body
    assert "/community/tips/article-id" not in plain_body


@pytest.mark.asyncio
async def test_published_article_email_still_renders_without_image(monkeypatch):
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(content_template, "send_email", send_email)

    await content_template.send_content_post_published_email(
        to_email="ada@example.com",
        member_name="Ada",
        post_id="article-id",
        title="Breathing Better",
    )

    html_body = send_email.await_args.args[3]
    assert "featured image" not in html_body
    assert "max-height:320px" not in html_body


@pytest.mark.asyncio
async def test_member_only_article_email_uses_protected_route(monkeypatch):
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(content_template, "send_email", send_email)

    await content_template.send_content_post_published_email(
        to_email="ada@example.com",
        member_name="Ada",
        post_id="club-article",
        title="Club Training",
        tier_access="club",
    )

    plain_body = send_email.await_args.args[2]
    assert "/community/tips/club-article" in plain_body
