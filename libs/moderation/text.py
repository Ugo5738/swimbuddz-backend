"""Text moderation via OpenAI's Moderation API.

Free, fast, best-in-class accuracy for harassment / safeguarding / self-harm.
Provider docs: https://platform.openai.com/docs/guides/moderation

Design notes:
  * Synchronous call. Keep it off the hot path — call before persist, but with a
    short timeout; in production, fall back to "flagged=False, requires_review=True"
    on provider error rather than dropping the message.
  * Threshold is configurable. Defaults here are conservative (0.5 for any
    hit) — tune per surface via the ``flag_threshold`` argument.
  * Never auto-deletes. Caller decides what to do with a flagged message.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from libs.moderation.types import (
    ModerationCategory,
    ModerationLabel,
    ModerationResult,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)

# Map OpenAI Moderation category keys → our coarse categories.
# Reference: https://platform.openai.com/docs/guides/moderation
_OPENAI_CATEGORY_MAP: dict[str, ModerationCategory] = {
    "sexual": ModerationCategory.SEXUAL,
    "sexual/minors": ModerationCategory.SAFEGUARDING,
    "harassment": ModerationCategory.HARASSMENT,
    "harassment/threatening": ModerationCategory.HARASSMENT,
    "hate": ModerationCategory.HATE,
    "hate/threatening": ModerationCategory.HATE,
    "self-harm": ModerationCategory.SELF_HARM,
    "self-harm/intent": ModerationCategory.SELF_HARM,
    "self-harm/instructions": ModerationCategory.SELF_HARM,
    "violence": ModerationCategory.VIOLENCE,
    "violence/graphic": ModerationCategory.VIOLENCE,
}


async def moderate_text(
    text: str,
    *,
    flag_threshold: float = 0.5,
    model: str = "omni-moderation-latest",
    api_key: Optional[str] = None,
) -> ModerationResult:
    """Moderate a string of text.

    Args:
        text: The text to moderate. Max length governed by the provider
              (tens of thousands of characters is fine).
        flag_threshold: Confidence score at which a label is considered
              sufficient to mark the whole result as flagged. Tune per surface.
        model: OpenAI moderation model to use.
        api_key: Explicit API key; falls back to ``OPENAI_API_KEY`` env var.

    Returns:
        ModerationResult with labels populated from the provider.

    Raises:
        ProviderUnavailableError: when credentials are missing or the call
            fails at the network level. Callers decide open/closed policy.
    """
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise ProviderUnavailableError(
            "OPENAI_API_KEY not set; text moderation is not configured."
        )

    # Import lazily so the module loads fine in environments without openai.
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise ProviderUnavailableError(
            "openai package not installed. Add it to pyproject.toml."
        ) from exc

    client = AsyncOpenAI(api_key=key)

    try:
        response = await client.moderations.create(model=model, input=text)
    except (
        Exception
    ) as exc:  # Provider errors are wrapped generically — callers decide.
        logger.warning("OpenAI moderation call failed: %s", exc)
        raise ProviderUnavailableError(str(exc)) from exc

    # Response shape: response.results[0].{flagged, categories, category_scores}
    # categories is a map of label -> bool, category_scores is label -> float.
    result_dict = response.results[0]
    category_scores: dict[str, float] = dict(result_dict.category_scores or {})

    labels: list[ModerationLabel] = []
    for provider_label, score in category_scores.items():
        mapped = _OPENAI_CATEGORY_MAP.get(provider_label, ModerationCategory.OTHER)
        labels.append(
            ModerationLabel(
                category=mapped,
                confidence=float(score),
                provider_label=provider_label,
            )
        )

    flagged = any(lbl.confidence >= flag_threshold for lbl in labels)

    return ModerationResult(
        flagged=flagged,
        labels=labels,
        provider="openai",
        raw=response.model_dump() if hasattr(response, "model_dump") else response,
    )
