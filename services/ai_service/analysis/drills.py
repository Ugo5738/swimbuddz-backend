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

import re
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


class TIDrill(TypedDict):
    """Curated, beginner-safe Total Immersion-informed drill (ADR-010 / ADR-013).

    Deterministic mapping from verified closed-enum (aspect, verdict) pairs so
    Stroke Lab never recommends unvetted or unsafe free-text drills.
    """

    key: str
    title: str
    ti_stage: str
    primary_focal_point: str
    why: str
    how: str
    safety_note: str
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

TI_DRILL_BANK: dict[str, TIDrill] = {
    "ti_superman_glide": {
        "key": "ti_superman_glide",
        "title": "Superman Glide (Eyes Down & Soft Neck)",
        "ti_stage": "balance_comfort",
        "primary_focal_point": "Release the weight of your head and look straight down so your hips float up.",
        "why": (
            "Lifting the head or letting the hips sink multiplies water drag "
            "and drains your energy before your arms even pull."
        ),
        "how": (
            "In shallow water, push off gently with arms forward shoulder-width "
            "apart, neck soft, eyes straight down, and glide calmly for 4–6 seconds "
            "until you feel your hips rise toward the surface."
        ),
        "safety_note": "Practise in shallow, standable water with relaxed breathing — stop and stand up well before you feel out of breath.",
        "academy_ref": "academy:ti:superman-glide",
    },
    "ti_one_goggle_breath": {
        "key": "ti_one_goggle_breath",
        "title": "One-Goggle / Pillow-Head Breathing",
        "ti_stage": "breathing_control",
        "primary_focal_point": "Keep your head resting on the water like a pillow as your body rotates to breathe.",
        "why": (
            "Lifting your head forward to grab air drops your hips and legs "
            "immediately, turning every breath into a brake."
        ),
        "how": (
            "Exhale steadily underwater, then let your head turn with your body "
            "roll so one goggle stays near the water surface while you inhale — "
            "never lift your chin forward down the lane."
        ),
        "safety_note": "Use short 15–25m repeats with full rest at the wall; never hold your breath to force extra distance.",
        "academy_ref": "academy:ti:one-goggle-breathing",
    },
    "ti_zen_skate": {
        "key": "ti_zen_skate",
        "title": "Zen Skate (Side-Lying Balance)",
        "ti_stage": "streamline_drag",
        "primary_focal_point": "Rotate hips and shoulders together onto your side to slip through a smaller hole in the water.",
        "why": (
            "Swimming flat on your belly increases frontal drag and forces your "
            "shoulders to do work your core rotation should carry."
        ),
        "how": (
            "Push off onto a comfortable side-lying angle (~45°) with your lead "
            "arm extended forward, top arm resting at your side, eyes down, and "
            "hold a calm gentle kick for 6–8 beats before switching sides."
        ),
        "safety_note": "Keep reps short in comfortable depth and roll gently to air whenever you need a breath.",
        "academy_ref": "academy:ti:zen-skate",
    },
    "ti_shark_fin_recovery": {
        "key": "ti_shark_fin_recovery",
        "title": "Shark Fin / Zipper Recovery",
        "ti_stage": "recovery",
        "primary_focal_point": "Elbow leads high and forward while the forearm and hand hang completely relaxed.",
        "why": (
            "A dropped, stiff, or wide swinging arm wastes energy out of the "
            "water and strains the shoulder."
        ),
        "how": (
            "During recovery, lead with the elbow while letting your fingertips "
            "hang loosely close to the water (like drawing a light zipper up "
            "your side), pausing briefly in a relaxed shark-fin shape before entry."
        ),
        "safety_note": "Keep the shoulder loose and pain-free — stop immediately if you feel pinching at the front of the shoulder.",
        "academy_ref": "academy:ti:shark-fin-recovery",
    },
    "ti_patient_lead_hand": {
        "key": "ti_patient_lead_hand",
        "title": "Patient Lead Hand / SpearSkate",
        "ti_stage": "streamline_drag",
        "primary_focal_point": "Keep your lead arm extended out front until your recovering hand is ready to enter.",
        "why": (
            "Dropping the lead hand early collapses your front-quadrant length "
            "and makes each stroke feel hurried and short."
        ),
        "how": (
            "Swim relaxed 25m lengths focusing on leaving your extended lead "
            "hand quiet and forward while the opposite elbow recovers, then "
            "spearing the entering hand cleanly into a long skate line."
        ),
        "safety_note": "Swim slowly enough to feel the position cleanly; pause at the wall between repeats.",
        "academy_ref": "academy:ti:patient-lead-hand",
    },
    "ti_mail_slot_entry": {
        "key": "ti_mail_slot_entry",
        "title": "Mail-Slot Entry (Shoulder-Track Spear)",
        "ti_stage": "streamline_drag",
        "primary_focal_point": "Slip fingertips quietly into the water in front of your shoulder and extend along your own shoulder track.",
        "why": (
            "Overreaching or slapping down creates splash, slows your forward "
            "glide, and pulls your body off its straight line."
        ),
        "how": (
            "Imagine sliding your hand through a narrow mail slot just ahead of "
            "your shoulder line with fingers angled slightly down, then letting "
            "the arm lengthen smoothly underwater."
        ),
        "safety_note": "Keep the entry soft and unhurried; avoid forcing shoulder hyper-extension.",
        "academy_ref": "academy:ti:mail-slot-entry",
    },
}

# Closed-enum (aspect, verdict) -> TI drill key. Used deterministically so the
# aggregator/verifier grounds every priority fix to a vetted, water-safe drill.
_ASPECT_VERDICT_TO_TI_DRILL: dict[tuple[str, str], str] = {
    ("body_line", "hips_low"): "ti_superman_glide",
    ("body_line", "legs_low"): "ti_superman_glide",
    ("body_line", "piked"): "ti_superman_glide",
    ("body_line", "arched"): "ti_superman_glide",
    ("head_breath", "lifted"): "ti_one_goggle_breath",
    ("body_rotation", "limited"): "ti_zen_skate",
    ("recovery_elbow", "dropped"): "ti_shark_fin_recovery",
    ("recovery_elbow", "wide"): "ti_shark_fin_recovery",
    ("entry_reach", "short"): "ti_patient_lead_hand",
    ("entry_reach", "overreach"): "ti_mail_slot_entry",
}

_ASPECT_DEFAULT_TI_DRILL: dict[str, str] = {
    "body_line": "ti_superman_glide",
    "head_breath": "ti_one_goggle_breath",
    "body_rotation": "ti_zen_skate",
    "recovery_elbow": "ti_shark_fin_recovery",
    "entry_reach": "ti_patient_lead_hand",
}

# Phrases that violate ADR-013 safety or TI efficiency-first doctrine in drills.
_UNSAFE_DRILL_PATTERNS = re.compile(
    r"(hold\s+your\s+breath|hypoxic|no[-\s]?breath|underwater\s+25|pull\s+harder|kick\s+harder|swim\s+until\s+exhaust)",
    re.I,
)


def is_safe_drill_text(text: Optional[str]) -> bool:
    """Return False if free-text drill copy violates water-safety or TI doctrine."""
    if not text or not text.strip():
        return False
    return _UNSAFE_DRILL_PATTERNS.search(text) is None


def resolve_drill(drill_key: Optional[str]) -> Optional[Drill]:
    """Look up a legacy observation drill by key. Returns None if unknown."""
    if not drill_key:
        return None
    return DRILL_BANK.get(drill_key)


def resolve_ti_drill(
    aspect: Optional[str],
    verdict: Optional[str] = None,
    *,
    discipline: str = "general",
) -> Optional[TIDrill]:
    """Deterministically map a verified (aspect, verdict) to a safe TI drill."""
    _ = discipline  # reserved for sprint-specific curated drills when unlocked
    if not aspect:
        return None
    key = None
    if verdict:
        key = _ASPECT_VERDICT_TO_TI_DRILL.get((aspect, verdict))
    if key is None:
        key = _ASPECT_DEFAULT_TI_DRILL.get(aspect)
    if key is None:
        return None
    return TI_DRILL_BANK.get(key)


def format_ti_drill_line(drill: TIDrill) -> str:
    """Compact one-line coaching prescription suitable for summary cards."""
    return f"{drill['title']} — {drill['primary_focal_point']}"
