"""Shared reporting location aliases; pool IDs remain available separately."""


def canonical_location(name: str | None) -> tuple[str, str]:
    """Collapse known cross-service location aliases into one operating site."""
    raw = (name or "Unspecified").strip()
    lowered = raw.lower()
    if lowered in {"vi", "v.i.", "v.i"}:
        return "victoria-island", "Victoria Island — Oduduwa House Pool"

    aliases = (
        (("rowe park", "yaba"), "yaba", "Yaba — Rowe Park Pool"),
        (
            ("oduduwa", "victoria island"),
            "victoria-island",
            "Victoria Island — Oduduwa House Pool",
        ),
        (("siloam", "festac"), "festac", "Festac — Siloam Pool"),
        (("herel", "ikoyi"), "ikoyi", "Ikoyi — Herel Play"),
        (("ikeja",), "ikeja", "Ikeja"),
        (("ogudu",), "ogudu", "Ogudu GRA"),
    )
    for needles, key, label in aliases:
        if any(needle in lowered for needle in needles):
            return key, label
    return lowered or "unspecified", raw or "Unspecified"
