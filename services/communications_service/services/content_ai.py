"""AI-assisted content draft generation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import litellm
from libs.common.config import get_settings
from libs.common.logging import get_logger

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


def _clean_text(value: Any, *, max_chars: int | None = None) -> str:
    if not isinstance(value, str):
        return ""

    text = " ".join(value.replace("\x00", "").split()).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0].rstrip(".,;: ")
        if text:
            text = f"{text}."
    return text


def _extract_json_text(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _message_content(response: Any) -> str:
    choices = response.get("choices") if isinstance(response, dict) else None
    if choices is None:
        choices = getattr(response, "choices", None)
    if not choices:
        return ""

    choice = choices[0]
    message = choice.get("message") if isinstance(choice, dict) else None
    if message is None:
        message = getattr(choice, "message", None)
    if message is None:
        return ""

    content = message.get("content") if isinstance(message, dict) else None
    if content is None:
        content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _parse_payload(raw_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(_extract_json_text(raw_text))
    except json.JSONDecodeError as exc:
        raise ContentAIDraftError("AI draft response was not valid JSON.") from exc

    if not isinstance(payload, dict):
        raise ContentAIDraftError("AI draft response must be a JSON object.")
    return payload


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


def _draft_from_ai_text(raw_text: str, *, title: str) -> GeneratedContentDraft:
    payload = _parse_payload(raw_text)
    summary = _clean_text(payload.get("summary"), max_chars=MAX_SUMMARY_CHARS)
    if not summary:
        summary = f"A practical SwimBuddz article draft about {title}."

    return GeneratedContentDraft(
        summary=summary,
        body=_blocks_from_payload(payload, title=title),
    )


def _build_messages(
    *,
    title: str,
    category: str,
    tier_access: str,
    brief: str | None,
) -> list[dict[str, str]]:
    context = (
        "SwimBuddz is an adult swimming community in Lagos. It serves community "
        "members, club members who want consistent practice, and academy members "
        "who are in structured cohorts. Articles should be practical, warm, "
        "safety-aware, and grounded in adult beginner and improver realities."
    )
    system = (
        "You write SwimBuddz article drafts for admin review. Be direct, useful, "
        "and specific to adult swimmers. Do not invent prices, schedules, "
        "policies, medical claims, or guarantees. For health, injury, panic, or "
        "safety topics, recommend getting qualified professional or coach support "
        "where appropriate. Return JSON only."
    )
    user = (
        f"{context}\n\n"
        f"Title: {title}\n"
        f"Category: {category}\n"
        f"Tier access: {tier_access}\n"
        f"Brief/context: {brief or 'Use the title and SwimBuddz context.'}\n\n"
        "Return this exact JSON shape with plain text only, no markdown:\n"
        "{\n"
        '  "summary": "One concise list-card summary, 160-260 characters.",\n'
        '  "sections": [\n'
        "    {\n"
        '      "heading": "Short section heading",\n'
        '      "paragraphs": ["1-3 useful paragraphs"],\n'
        '      "bullets": ["Optional short bullet points"]\n'
        "    }\n"
        "  ],\n"
        '  "closing": "Optional practical closing paragraph."\n'
        "}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def generate_content_draft(
    *,
    title: str,
    category: str,
    tier_access: str,
    brief: str | None = None,
) -> GeneratedContentDraft:
    """Generate an unpublished, human-reviewable content post draft."""

    settings = get_settings()
    model = getattr(settings, "AI_DEFAULT_MODEL", "gpt-4o-mini") or "gpt-4o-mini"
    litellm.drop_params = True

    try:
        response = await litellm.acompletion(
            model=model,
            messages=_build_messages(
                title=title,
                category=category,
                tier_access=tier_access,
                brief=brief,
            ),
            temperature=0.6,
            max_tokens=1800,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.warning("AI content draft generation failed: %s", exc, exc_info=True)
        raise ContentAIDraftError(
            "AI draft generation is currently unavailable."
        ) from exc

    raw_text = _message_content(response)
    if not raw_text:
        raise ContentAIDraftError("AI draft response was empty.")

    return _draft_from_ai_text(raw_text, title=title)
