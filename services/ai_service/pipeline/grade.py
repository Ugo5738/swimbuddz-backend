"""Deterministic discipline re-grade — the $0, code-side half of goal-awareness.

The VLM emits an HONEST, discipline-blind closed-enum verdict per aspect (e.g.
body_line='hips_low', entry_reach='clean_extended'). ``grade`` maps that verdict
to a (severity, rank) for the swimmer's discipline (design §12.3). It NEVER
invents or suppresses what was seen — it only decides how a visible truth is
prioritised and framed. Pure + exhaustively unit-tested (the cheap place to make
goal-awareness bulletproof); no VLM call, so re-grading a cached run is free.

Because discipline lives here (not in the VLM cache key), a swimmer can re-run
"as a sprinter" vs "as a distance swimmer" and the SAME cached verdict re-grades
for $0 (run-store-reuse).
"""

from __future__ import annotations

from typing import Optional

from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    SEVERITY_INFO,
    SEVERITY_STRENGTH,
    CoachContext,
)

# rank: lower = higher priority (1 = top fix). strengths/info sort below fixes.
_R_TOP = 1  # the highest-impact adult faults (head lift, body-line sink for distance)
_R_PROP = 2  # propulsion fixes (dropped elbow, sink under sprint)
_R_MINOR = 3  # smaller efficiency fixes (short reach)
_R_STRENGTH = 50  # genuine strengths — shown, but below every fix
_R_INFO = 60  # neutral observations
_R_UNCLEAR = 70  # unclear / nothing-to-say

FIX, STRENGTH, INFO = SEVERITY_FIX, SEVERITY_STRENGTH, SEVERITY_INFO

# A body-line sink is rank-1 for distance/general (it's what tires an adult and
# kills efficiency) but ranks below propulsion for a sprinter who can muscle
# through some drag.
_SINK = {
    "sprint": (FIX, _R_PROP),
    "distance": (FIX, _R_TOP),
    "general": (FIX, _R_TOP),
}

# (aspect, verdict) -> either a neutral (severity, rank) tuple [graded the same for
# every discipline], or a {discipline: (severity, rank)} dict [discipline-dependent;
# a missing discipline falls back to the "general" entry].
_GRADES: dict[tuple[str, str], object] = {
    # ── body_line ── (hips/legs/pike/arch sink graded by _SINK; flat = strength)
    ("body_line", "hips_low"): _SINK,
    ("body_line", "legs_low"): _SINK,
    ("body_line", "piked"): _SINK,
    ("body_line", "arched"): _SINK,
    ("body_line", "flat"): (STRENGTH, _R_STRENGTH),
    ("body_line", "unclear"): (INFO, _R_UNCLEAR),
    # ── recovery_elbow ──
    ("recovery_elbow", "dropped"): (FIX, _R_PROP),  # neutral fault — drag + shoulder
    ("recovery_elbow", "wide"): {  # sprinters trade width for tempo
        "sprint": (INFO, _R_INFO),
        "distance": (FIX, _R_PROP),
        "general": (FIX, _R_PROP),
    },
    ("recovery_elbow", "high"): (STRENGTH, _R_STRENGTH),
    ("recovery_elbow", "unclear"): (INFO, _R_UNCLEAR),
    # ── body_rotation (shoulder/hip roll side-to-side) ──
    ("body_rotation", "good"): (STRENGTH, _R_STRENGTH),
    (
        "body_rotation",
        "limited",
    ): {  # flat swimming = drag + weak catch; worst for distance
        "sprint": (FIX, _R_PROP),
        "distance": (FIX, _R_TOP),
        "general": (FIX, _R_TOP),
    },
    ("body_rotation", "unclear"): (INFO, _R_UNCLEAR),
    # ── head_breathing → area "head_breath" ──
    ("head_breath", "lifted"): (FIX, _R_TOP),  # highest-impact adult fault (sinks legs)
    ("head_breath", "neutral"): (STRENGTH, _R_STRENGTH),
    ("head_breath", "unclear"): (INFO, _R_UNCLEAR),
    # breath SIDE is always an observation, never a fault (honesty rule)
    ("head_breath", "left"): (INFO, _R_INFO),
    ("head_breath", "right"): (INFO, _R_INFO),
    ("head_breath", "both"): (INFO, _R_INFO),
    ("head_breath", "none_seen"): (INFO, _R_UNCLEAR),
    # ── entry_reach ── (the headline goal-awareness flip)
    ("entry_reach", "clean_extended"): {
        # long front extension: free distance for distance/general, but for a
        # sprinter only a HEDGED info note (a dead-spot lives between frames →
        # never a hard fix; the analyzer also caps confidence ≤0.5)
        "sprint": (INFO, _R_INFO),
        "distance": (STRENGTH, _R_STRENGTH),
        "general": (STRENGTH, _R_STRENGTH),
    },
    ("entry_reach", "short"): {
        "sprint": (INFO, _R_INFO),  # fine for high tempo
        "distance": (FIX, _R_MINOR),
        "general": (FIX, _R_MINOR),
    },
    ("entry_reach", "overreach"): (INFO, _R_INFO),  # info only — never "crossover"
    ("entry_reach", "unclear"): (INFO, _R_UNCLEAR),
}


def grade(
    aspect: str, verdict: str, coaching: Optional[CoachContext] = None
) -> tuple[str, int]:
    """Map an honest closed-enum (aspect, verdict) → (severity, rank) for the
    swimmer's discipline. Unknown aspect/verdict → a safe INFO (never a fault).
    ``focus_area`` lifts its aspect to the top without changing severity/honesty."""
    coaching = coaching or CoachContext()
    disc = (
        coaching.discipline
        if coaching.discipline in ("sprint", "distance", "general")
        else "general"
    )

    entry = _GRADES.get((aspect, verdict))
    if entry is None:
        severity, rank = INFO, _R_UNCLEAR  # unknown verdict is never a fault
    elif isinstance(entry, dict):
        severity, rank = entry.get(disc, entry["general"])
    else:
        severity, rank = entry

    # The swimmer explicitly asked about this aspect → surface it first. Severity
    # (and therefore honesty) is unchanged; only priority moves.
    if coaching.focus_area == aspect:
        rank = 0 if severity != INFO else min(rank, _R_MINOR)

    return severity, rank
