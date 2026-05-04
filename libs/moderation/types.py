"""Provider-agnostic moderation result types."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class ModerationCategory(str, enum.Enum):
    """Coarse categories we care about across providers.

    Providers use their own taxonomies; implementations map to these buckets.
    Keep this list small — add categories only when downstream policy uses them.
    """

    SAFEGUARDING = "safeguarding"  # Child-safety / sexual content involving minors
    SEXUAL = "sexual"  # Adult sexual content
    HARASSMENT = "harassment"
    HATE = "hate"
    SELF_HARM = "self_harm"
    VIOLENCE = "violence"
    # "Suggestive" is separated from "sexual" because pool photos will often
    # hit the suggestive category; downstream policy should treat them differently.
    SUGGESTIVE = "suggestive"
    OTHER = "other"


@dataclass
class ModerationLabel:
    """A single category label with a provider-reported confidence score."""

    category: ModerationCategory
    confidence: float  # 0.0 .. 1.0
    provider_label: str = ""  # Original provider-specific label, for audit


@dataclass
class ModerationResult:
    """Outcome of a moderation check.

    Attributes:
        flagged: Whether *any* label crosses the configured threshold.
                 This is a **policy decision**, not a verdict — always route
                 flagged content to manual review; never auto-delete.
        labels: Every category with a non-trivial confidence score from the
                provider (caller may still want to inspect low-confidence labels
                for debugging).
        provider: Provider identifier, e.g. ``"openai"``, ``"aws_rekognition"``.
        raw: Full provider response, retained for audit/debugging. Callers
             should not rely on its shape — treat as opaque.
    """

    flagged: bool
    labels: list[ModerationLabel] = field(default_factory=list)
    provider: str = ""
    raw: Any = None

    def top_label(self) -> ModerationLabel | None:
        """Highest-confidence label, or None if no labels were returned."""
        return max(self.labels, key=lambda lbl: lbl.confidence, default=None)


class ProviderUnavailableError(RuntimeError):
    """Raised when a moderation provider is missing credentials or unreachable.

    Callers should treat this as **open by default** in dev, and **fail closed**
    in production (e.g. hold message in pending state rather than deliver).
    Exact policy is decided per surface, not here.
    """
