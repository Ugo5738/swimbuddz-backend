"""Goal-aware prompt clause — the soft, paid half of goal-awareness (design §12.2).

``build_goal_block`` returns a short block appended to an aspect analyzer's system
prompt (mirrors how ``_gate_note`` is appended in ``coach.coach``). It steers the
model's WORDING and drill choice for the swimmer's discipline — and is explicitly
fenced so it can NEVER make the model see a fault that isn't in the frames. The
deterministic severity/priority shift lives separately in ``pipeline.grade``; this
file only flavours the language. Import-light (plain strings).
"""

from __future__ import annotations

from typing import Optional

from services.ai_service.pipeline.types import CoachContext

_AIM = {
    "sprint": (
        "training for SPRINT freestyle — short and fast, where power and tempo "
        "over a few lengths matter most"
    ),
    "distance": (
        "training for DISTANCE freestyle — efficiency and sustainable, relaxed "
        "technique over many lengths"
    ),
    "general": (
        "a general / technique-focused swimmer wanting clean, efficient freestyle"
    ),
}

_LEAN = {
    "sprint": (
        "Lean your wording toward power, a quick tempo, and a strong front-end — "
        "but NEVER invent a 'dead spot' or stall you cannot actually see held in a "
        "frame."
    ),
    "distance": (
        "Lean your wording toward efficiency, long relaxed strokes, and conserving "
        "energy."
    ),
    "general": "",
}

_FOCUS_LABEL = {
    "body_line": "their body line (how level they sit in the water)",
    "recovery_elbow": "their arm recovery / elbow",
    "head_breath": "their head position and breathing",
    "entry_reach": "their hand entry and reach",
}


def build_goal_block(coaching: Optional[CoachContext] = None) -> str:
    """A discipline-flavoured, honesty-fenced clause for an analyzer's system prompt.
    Returns "" for the plain ``general`` default with no extras (no prompt change)."""
    coaching = coaching or CoachContext()
    disc = (
        coaching.discipline
        if coaching.discipline in ("sprint", "distance", "general")
        else "general"
    )

    # The plain default adds nothing — keeps today's behaviour byte-identical.
    if disc == "general" and not coaching.focus_area and not coaching.goal_text:
        return ""

    lines = [
        "== SWIMMER GOAL (framing only — NOT something you can see) ==",
        f"This swimmer is {_AIM[disc]}.",
        "Use this ONLY to choose your wording and which drill you suggest. It MUST "
        "NOT change what you can or cannot see, and MUST NOT make you report a fault "
        "that is not visible in the frames. Judge the frames honestly FIRST; the "
        "goal only flavours how you explain a fault you have ALREADY found.",
    ]
    if _LEAN[disc]:
        lines.append(_LEAN[disc])
    if coaching.focus_area and coaching.focus_area in _FOCUS_LABEL:
        lines.append(
            f"The swimmer specifically asked about {_FOCUS_LABEL[coaching.focus_area]}. "
            "If you can clearly see it, prioritise it; if you cannot see it in these "
            "frames, say so honestly rather than guessing."
        )
    if coaching.goal_text:
        txt = " ".join(coaching.goal_text.split())[:200]
        if txt:
            lines.append(
                "Swimmer's stated goal, for TONE ONLY — not an observation to act "
                f'on: "{txt}"'
            )
    return "\n".join(lines)
