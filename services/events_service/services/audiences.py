"""Canonical event audience normalization during the scalar-field transition."""

from collections.abc import Iterable

VALID_EVENT_AUDIENCES = ("community", "club", "academy")


def normalize_event_audiences(
    *,
    primary_audience: str | None = None,
    audiences: Iterable[str] | None = None,
    audience: str | None = None,
) -> tuple[str, list[str]]:
    """Return one presentation lane and a stable, de-duplicated relevance list.

    ``audience`` is accepted only as the legacy input alias. The returned
    primary value is always included in the relevance list.
    """

    primary = (primary_audience or audience or "community").strip().lower()
    if primary not in VALID_EVENT_AUDIENCES:
        raise ValueError("Audience must be Community, Club, or Academy")

    normalized: list[str] = []
    for value in audiences or []:
        candidate = str(value).strip().lower()
        if candidate not in VALID_EVENT_AUDIENCES:
            raise ValueError("Audience must be Community, Club, or Academy")
        if candidate not in normalized:
            normalized.append(candidate)
    if primary not in normalized:
        normalized.insert(0, primary)
    return primary, normalized


def audience_fields(
    *,
    primary_audience: str | None = None,
    audiences: Iterable[str] | None = None,
    audience: str | None = None,
) -> dict[str, object]:
    """Build model fields with the deprecated scalar kept in sync."""

    primary, relevant = normalize_event_audiences(
        primary_audience=primary_audience,
        audiences=audiences,
        audience=audience,
    )
    return {
        "primary_audience": primary,
        "audiences": relevant,
        "audience": primary,
    }
