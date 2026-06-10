"""Deterministic drill bank for Stroke Lab observations.

v0 is a small built-in placeholder set keyed by the technique issue the
pipeline detects. When the real Academy drill bank is ready, swap the
values here (or point ``academy_ref`` at Academy content IDs) — the
observation engine and the API only depend on the keys, not the copy.

Intentionally NOT LLM-generated: drill advice to beginner swimmers in
water is a safety matter, so the wording is fixed, conservative, and
always framed as "explore with a coach" rather than prescriptive.
"""

from __future__ import annotations

from typing import Optional, TypedDict


class Drill(TypedDict):
    key: str
    title: str
    why: str
    how: str
    # Placeholder for a future Academy content reference (lesson/drill id).
    # When the Academy bank lands, populate this and the frontend can deep
    # link into the curriculum.
    academy_ref: Optional[str]


DRILL_BANK: dict[str, Drill] = {
    "low_rotation": {
        "key": "low_rotation",
        "title": "Side-kick rotation drill",
        "why": (
            "Rotating from the core lets you reach further each stroke and "
            "makes breathing easier — flat swimming costs you distance."
        ),
        "how": (
            "On your side, bottom arm extended, kick gently and hold the "
            "position for 6–10 kicks, then roll to the other side. Feel the "
            "rotation come from your hips, not your shoulders."
        ),
        "academy_ref": None,
    },
    "one_sided_breathing": {
        "key": "one_sided_breathing",
        "title": "Bilateral breathing (breathe every 3)",
        "why": (
            "Breathing to only one side builds an uneven stroke and a tight "
            "neck. Alternating keeps your stroke balanced and your line "
            "straight."
        ),
        "how": (
            "Breathe every 3rd arm pull so you alternate sides. Start with "
            "short reps and build up — it feels awkward before it feels "
            "natural."
        ),
        "academy_ref": None,
    },
    "knee_driven_kick": {
        "key": "knee_driven_kick",
        "title": "Kick from the hips (vertical kick / kick-on-back)",
        "why": (
            "A big knee bend (bicycle kick) creates drag and tires you out. "
            "An efficient flutter kick is driven from the hips and glutes "
            "with relatively straight legs."
        ),
        "how": (
            "Kick on your back with arms at your sides, legs long, small fast "
            "kicks from the hips. Watch that your knees stay soft, not "
            "bending sharply."
        ),
        "academy_ref": None,
    },
    "high_stroke_rate": {
        "key": "high_stroke_rate",
        "title": "Catch-up drill (lengthen each stroke)",
        "why": (
            "A very high stroke rate can mean short, hurried pulls that don't "
            "grab much water. Lengthening each stroke often adds speed with "
            "less effort."
        ),
        "how": (
            "Touch your hands together out front before each pull, so one arm "
            "waits for the other. Focus on reaching and a clean catch."
        ),
        "academy_ref": None,
    },
}


def resolve_drill(drill_key: Optional[str]) -> Optional[Drill]:
    """Look up a drill by key. Returns None for unknown / no drill."""
    if not drill_key:
        return None
    return DRILL_BANK.get(drill_key)
