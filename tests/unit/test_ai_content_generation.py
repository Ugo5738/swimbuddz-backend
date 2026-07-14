import json

import pytest

import services.ai_service.generation.content as generation
from services.ai_service.context import SWIMBUDDZ_CONTENT_CONTEXT_VERSION
from services.ai_service.providers.base import AIProviderResponse
from services.ai_service.schemas.content import ContentDraftRequest


@pytest.mark.asyncio
async def test_article_generation_includes_versioned_full_context(monkeypatch):
    async def fake_call_llm(**kwargs):
        system = kwargs["system_prompt"]
        assert SWIMBUDDZ_CONTENT_CONTEXT_VERSION in system
        assert "Club pods are small peer-led practice groups" in system
        assert "Academy is structured learning" in system
        assert "Never invent" in system
        assert kwargs["response_format"] == {"type": "json_object"}
        return AIProviderResponse(
            content=json.dumps(
                {
                    "summary": "A practical guide for adults building calm pool confidence.",
                    "sections": [
                        {
                            "heading": "Start at the wall",
                            "paragraphs": ["Practise a slow exhale in shallow water."],
                            "bullets": [],
                        }
                    ],
                    "closing": "Ask a qualified coach for support when needed.",
                    "featured_image_prompt": (
                        "Adult African swimmers practising safely at a Lagos pool wall."
                    ),
                }
            ),
            model="test-model",
            provider="test",
        )

    monkeypatch.setattr(generation, "call_llm", fake_call_llm)

    payload, response = await generation.generate_article_draft(
        ContentDraftRequest(
            title="Build calm pool confidence",
            category="getting_started",
            tier_access="community",
        )
    )

    assert payload.sections[0].heading == "Start at the wall"
    assert response.model == "test-model"


@pytest.mark.asyncio
async def test_article_generation_rejects_invalid_provider_shape(monkeypatch):
    async def fake_call_llm(**kwargs):
        return AIProviderResponse(
            content='{"summary": "too short"}',
            model="test-model",
            provider="test",
        )

    monkeypatch.setattr(generation, "call_llm", fake_call_llm)

    with pytest.raises(generation.ContentGenerationError):
        await generation.generate_article_draft(
            ContentDraftRequest(title="Build calm pool confidence")
        )
