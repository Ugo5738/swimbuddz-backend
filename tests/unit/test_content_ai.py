import json
import uuid
from types import SimpleNamespace

import pytest

import services.communications_service.routers.content as content_router_module
from libs.common.datetime_utils import utc_now
from services.communications_service.routers.content import create_ai_content_draft
from services.communications_service.schemas import (
    ContentAIDraftCreate,
    ContentPostResponse,
)
from services.communications_service.services import content_ai
from services.communications_service.services.content_ai import (
    ContentAIDraftError,
    GeneratedContentDraft,
    generate_content_draft,
)


@pytest.mark.asyncio
async def test_generate_content_draft_returns_blocknote_json(monkeypatch):
    async def fake_acompletion(**kwargs):
        assert kwargs["model"] == "test-model"
        assert kwargs["response_format"] == {"type": "json_object"}
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "A practical breathing guide.",
                                "sections": [
                                    {
                                        "heading": "Start with calm exhales",
                                        "paragraphs": [
                                            "Breathe out slowly into the water first."
                                        ],
                                        "bullets": ["Practise at the wall."],
                                    }
                                ],
                                "closing": "Review this with a coach if panic persists.",
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(
        content_ai,
        "get_settings",
        lambda: SimpleNamespace(AI_DEFAULT_MODEL="test-model"),
    )
    monkeypatch.setattr(content_ai.litellm, "acompletion", fake_acompletion)

    draft = await generate_content_draft(
        title="How to breathe calmly",
        category="breathing",
        tier_access="community",
        brief="For adult beginners.",
    )

    blocks = json.loads(draft.body)
    assert draft.summary == "A practical breathing guide."
    assert blocks[0]["type"] == "heading"
    assert blocks[0]["content"][0]["text"] == "Start with calm exhales"
    assert blocks[1]["type"] == "paragraph"
    assert blocks[2]["type"] == "bulletListItem"
    assert (
        blocks[3]["content"][0]["text"] == "Review this with a coach if panic persists."
    )


@pytest.mark.asyncio
async def test_generate_content_draft_accepts_json_code_fence(monkeypatch):
    async def fake_acompletion(**kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            "```json\n"
                            '{"summary": "Short summary.", '
                            '"sections": [{"paragraphs": ["Draft body."]}]}\n'
                            "```"
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(
        content_ai,
        "get_settings",
        lambda: SimpleNamespace(AI_DEFAULT_MODEL="test-model"),
    )
    monkeypatch.setattr(content_ai.litellm, "acompletion", fake_acompletion)

    draft = await generate_content_draft(
        title="Pool confidence",
        category="safety",
        tier_access="community",
    )

    blocks = json.loads(draft.body)
    assert draft.summary == "Short summary."
    assert blocks[0]["type"] == "paragraph"
    assert blocks[0]["content"][0]["text"] == "Draft body."


@pytest.mark.asyncio
async def test_generate_content_draft_wraps_provider_failures(monkeypatch):
    async def fake_acompletion(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        content_ai,
        "get_settings",
        lambda: SimpleNamespace(AI_DEFAULT_MODEL="test-model"),
    )
    monkeypatch.setattr(content_ai.litellm, "acompletion", fake_acompletion)

    with pytest.raises(ContentAIDraftError):
        await generate_content_draft(
            title="Pool confidence",
            category="safety",
            tier_access="community",
        )


@pytest.mark.asyncio
async def test_ai_content_draft_route_saves_unpublished_without_email(monkeypatch):
    body = json.dumps(
        [
            {
                "id": "1",
                "type": "paragraph",
                "props": {
                    "textColor": "default",
                    "backgroundColor": "default",
                    "textAlignment": "left",
                },
                "content": [{"type": "text", "text": "Draft body.", "styles": {}}],
                "children": [],
            }
        ]
    )
    created_by = uuid.uuid4()

    class FakeDB:
        added_post = None
        committed = False

        def add(self, post):
            self.added_post = post

        async def commit(self):
            self.committed = True

        async def refresh(self, post):
            now = utc_now()
            post.id = uuid.uuid4()
            post.created_at = now
            post.updated_at = now

    async def fake_content_post_response(db, post):
        assert isinstance(db, FakeDB)
        assert db.committed is True
        assert db.added_post is post
        return ContentPostResponse(
            id=post.id,
            title=post.title,
            summary=post.summary,
            body=post.body,
            category=post.category,
            featured_image_media_id=post.featured_image_media_id,
            tier_access=post.tier_access,
            email_on_publish=post.email_on_publish,
            is_published=post.is_published,
            published_at=post.published_at,
            scheduled_for=post.scheduled_for,
            created_by=post.created_by,
            created_at=post.created_at,
            updated_at=post.updated_at,
            comment_count=0,
            featured_image_url=None,
            email_sent_count=0,
            email_failed_count=0,
            last_email_sent_at=None,
        )

    async def fake_generate_content_draft(**kwargs):
        assert kwargs == {
            "title": "How to swim twice a week",
            "category": "swimming_tips",
            "tier_access": "club",
            "brief": "For club members.",
        }
        return GeneratedContentDraft(summary="Generated summary.", body=body)

    monkeypatch.setattr(
        content_router_module,
        "generate_content_draft",
        fake_generate_content_draft,
    )
    monkeypatch.setattr(
        content_router_module,
        "_content_post_response",
        fake_content_post_response,
    )

    response = await create_ai_content_draft(
        ContentAIDraftCreate(
            title="How to swim twice a week",
            category="swimming_tips",
            tier_access="club",
            brief="For club members.",
        ),
        created_by=created_by,
        db=FakeDB(),
    )

    assert response.status == "draft"
    assert response.created_by == created_by
    assert response.is_published is False
    assert response.published_at is None
    assert response.scheduled_for is None
    assert response.email_on_publish is False
    assert response.summary == "Generated summary."
