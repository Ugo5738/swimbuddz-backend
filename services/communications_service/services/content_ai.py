"""AI-assisted content draft generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post

logger = get_logger(__name__)

DEFAULT_BLOCK_PROPS = {
    "textColor": "default",
    "backgroundColor": "default",
    "textAlignment": "left",
}
MAX_SUMMARY_CHARS = 320
MAX_HEADING_CHARS = 100
MAX_PARAGRAPH_CHARS = 900
MAX_BULLET_CHARS = 220
MAX_SECTIONS = 7
MAX_PARAGRAPHS_PER_SECTION = 3
MAX_BULLETS_PER_SECTION = 5


class ContentAIDraftError(RuntimeError):
    """Raised when an AI content draft cannot be generated."""


@dataclass(frozen=True)
class GeneratedContentDraft:
    """Generated fields for a content post draft."""

    summary: str
    body: str
    featured_image_prompt: str
    ai_request_id: str
    context_version: str
    model_used: str


def _clean_text(value: Any, *, max_chars: int | None = None) -> str:
    if not isinstance(value, str):
        return ""

    text = " ".join(value.replace("\x00", "").split()).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip(".,;: ")
        if text:
            text = f"{text}."
    return text


def _block(block_id: int, block_type: str, text: str, **props: Any) -> dict[str, Any]:
    block_props = {**DEFAULT_BLOCK_PROPS, **props}
    return {
        "id": str(block_id),
        "type": block_type,
        "props": block_props,
        "content": [{"type": "text", "text": text, "styles": {}}],
        "children": [],
    }


def _iter_clean_strings(values: Any, *, max_chars: int, limit: int) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []

    cleaned = []
    for value in values:
        text = _clean_text(value, max_chars=max_chars)
        if text:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _normalised_sections(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sections = payload.get("sections")
    if not isinstance(sections, list):
        return []

    normalised: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue

        heading = _clean_text(section.get("heading"), max_chars=MAX_HEADING_CHARS)
        paragraphs = _iter_clean_strings(
            section.get("paragraphs"),
            max_chars=MAX_PARAGRAPH_CHARS,
            limit=MAX_PARAGRAPHS_PER_SECTION,
        )
        bullets = _iter_clean_strings(
            section.get("bullets"),
            max_chars=MAX_BULLET_CHARS,
            limit=MAX_BULLETS_PER_SECTION,
        )
        if heading or paragraphs or bullets:
            normalised.append(
                {"heading": heading, "paragraphs": paragraphs, "bullets": bullets}
            )
        if len(normalised) >= MAX_SECTIONS:
            break

    return normalised


def _blocks_from_payload(payload: dict[str, Any], *, title: str) -> str:
    sections = _normalised_sections(payload)
    closing = _clean_text(payload.get("closing"), max_chars=MAX_PARAGRAPH_CHARS)
    blocks: list[dict[str, Any]] = []
    block_id = 1

    if not sections:
        fallback = _clean_text(payload.get("body") or payload.get("article"))
        if not fallback:
            fallback = _clean_text(
                payload.get("summary"), max_chars=MAX_PARAGRAPH_CHARS
            )
        sections = [
            {
                "heading": "",
                "paragraphs": [fallback or f"Draft article for {title}."],
                "bullets": [],
            }
        ]

    for section in sections:
        if section["heading"]:
            blocks.append(_block(block_id, "heading", section["heading"], level=3))
            block_id += 1

        for paragraph in section["paragraphs"]:
            blocks.append(_block(block_id, "paragraph", paragraph))
            block_id += 1

        for bullet in section["bullets"]:
            blocks.append(_block(block_id, "bulletListItem", bullet))
            block_id += 1

    if closing:
        blocks.append(_block(block_id, "paragraph", closing))

    return json.dumps(blocks, ensure_ascii=False)


def _draft_from_payload(
    payload: dict[str, Any], *, title: str
) -> GeneratedContentDraft:
    summary = _clean_text(payload.get("summary"), max_chars=MAX_SUMMARY_CHARS)
    if not summary:
        raise ContentAIDraftError("AI service returned an invalid article summary.")

    image_prompt = _clean_text(payload.get("featured_image_prompt"), max_chars=1200)
    ai_request_id = _clean_text(payload.get("ai_request_id"), max_chars=100)
    context_version = _clean_text(payload.get("context_version"), max_chars=100)
    model_used = _clean_text(payload.get("model_used"), max_chars=160)
    if not all((image_prompt, ai_request_id, context_version, model_used)):
        raise ContentAIDraftError("AI service returned an incomplete article draft.")

    return GeneratedContentDraft(
        summary=summary,
        body=_blocks_from_payload(payload, title=title),
        featured_image_prompt=image_prompt,
        ai_request_id=ai_request_id,
        context_version=context_version,
        model_used=model_used,
    )


async def generate_content_draft(
    *,
    title: str,
    category: str,
    tier_access: str,
    brief: str | None = None,
) -> GeneratedContentDraft:
    """Generate an unpublished, human-reviewable content post draft."""

    settings = get_settings()
    try:
        response = await internal_post(
            service_url=settings.AI_SERVICE_URL,
            path="/ai/content/drafts",
            calling_service="communications",
            json={
                "title": title,
                "category": category,
                "tier_access": tier_access,
                "brief": brief,
            },
            timeout=45.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("AI service draft request failed: %s", exc, exc_info=True)
        raise ContentAIDraftError(
            "AI draft generation is currently unavailable."
        ) from exc

    if not isinstance(payload, dict):
        raise ContentAIDraftError("AI service returned an invalid article draft.")

    return _draft_from_payload(payload, title=title)
