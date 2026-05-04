"""SwimBuddz content moderation wrappers.

Thin interfaces over third-party moderation providers. Keep the surface small
and provider-agnostic so consumers can swap implementations behind a feature
flag if needed.

Providers:
  - OpenAI Moderation API — text moderation (free, fast). See ``libs.moderation.text``.
  - AWS Rekognition DetectModerationLabels — image/video moderation (paid).
    See ``libs.moderation.image``.

Core design rule — swim-context tuning (see
``docs/design/CHAT_SERVICE_DESIGN.md`` §6.1 rule 6):

  * Results are confidence scores, NEVER verdicts.
  * Thresholds are tunable by ``safeguarding_admin``, not hard-coded.
  * Never auto-delete flagged content — always quarantine to manual review.
  * Generic moderators WILL false-positive on children in swimwear; design
    every consumer around that assumption.

Usage sketch::

    from libs.moderation import ModerationResult, moderate_text, moderate_image

    result = await moderate_text(body)
    if result.flagged:
        # → quarantine message; enqueue in safeguarding review queue
        ...
"""

from libs.moderation.image import moderate_image  # noqa: F401
from libs.moderation.text import moderate_text  # noqa: F401
from libs.moderation.types import (  # noqa: F401
    ModerationCategory,
    ModerationLabel,
    ModerationResult,
    ProviderUnavailableError,
)

__all__ = [
    "moderate_text",
    "moderate_image",
    "ModerationResult",
    "ModerationLabel",
    "ModerationCategory",
    "ProviderUnavailableError",
]
