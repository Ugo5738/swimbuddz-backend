"""Article generation using the canonical SwimBuddz editorial context."""

from pydantic import ValidationError

from services.ai_service.context import (
    SWIMBUDDZ_CONTENT_CONTEXT,
    SWIMBUDDZ_CONTENT_CONTEXT_VERSION,
)
from services.ai_service.providers.base import AIProviderResponse, call_llm
from services.ai_service.schemas.content import ContentDraftPayload, ContentDraftRequest


class ContentGenerationError(RuntimeError):
    """Raised when a provider cannot produce a valid article draft."""


async def generate_article_draft(
    request: ContentDraftRequest,
    *,
    model: str | None = None,
) -> tuple[ContentDraftPayload, AIProviderResponse]:
    system_prompt = (
        "You create unpublished SwimBuddz article drafts for human admin review. "
        "Follow the canonical context exactly. Return JSON only, with no markdown "
        "fences. Never invent dynamic operational facts.\n\n"
        f"CONTEXT VERSION: {SWIMBUDDZ_CONTENT_CONTEXT_VERSION}\n\n"
        f"CANONICAL SWIMBUDDZ CONTEXT:\n{SWIMBUDDZ_CONTENT_CONTEXT}"
    )
    user_prompt = (
        f"Title: {request.title}\n"
        f"Category: {request.category}\n"
        f"Audience tier: {request.tier_access}\n"
        f"Admin brief: {request.brief or 'Use the title and canonical context.'}\n\n"
        "Write a useful article draft. Return exactly this JSON shape:\n"
        "{\n"
        '  "summary": "A concise 160-260 character summary",\n'
        '  "sections": [{"heading": "Short heading", "paragraphs": '
        '["One to three paragraphs"], "bullets": ["Optional short points"]}],\n'
        '  "closing": "A practical optional closing",\n'
        '  "featured_image_prompt": "A specific editorial image prompt"\n'
        "}"
    )

    try:
        ai_response = await call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            temperature=0.6,
            max_tokens=2200,
            response_format={"type": "json_object"},
            trace_name="content_article_draft",
        )
        payload = ContentDraftPayload.model_validate(ai_response.parse_json())
    except (ValueError, ValidationError) as exc:
        raise ContentGenerationError(
            "AI provider returned an invalid article draft."
        ) from exc

    return payload, ai_response
