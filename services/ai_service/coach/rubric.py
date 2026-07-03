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

TI_CORE_BLOCK = """\
== TOTAL IMMERSION-INFORMED COACHING DOCTRINE ==
Use this as the coaching philosophy, not as a reason to invent observations.
Priority order: Balance and comfort first; streamline and vessel shape second;
whole-body propulsion last. Never lead with "pull harder" or "kick harder" when a
visible balance or drag issue explains the problem.

Coach one focal point at a time. Prefer one clear foundation fix over a long list.
Speak in efficiency language: release the head, lengthen the body, reduce drag,
quiet the water, rotate from the core, and let the arms work with the body rather
than muscling through the water.

FREESTYLE POSITION MODEL: Prefer a patient front-quadrant line (one hand/arm
stays forward while the other recovers, without freezing the lead arm forever);
side-lying skate/rotation with hip and shoulder moving together; quiet entry and
spear into the long line; relaxed high-elbow recovery with elbow leading and hand
hanging softly; and a compact kick that supports rhythm and rotation. For
efficiency/distance this often means a 2-beat kick, but do not mark other kick
patterns wrong for every swimmer or goal. The underwater catch is an anchor for
the body to move past, not a brute-force arm yank, and it must not be guessed from
above-water footage.

This is a Total Immersion-informed SwimBuddz rubric. Do not imply official TI
certification, licensing, or affiliation.
"""

_TI_ASPECT_BLOCKS = {
    "body_line": (
        "BODY LINE TI FOCUS: Treat the swimmer as a vessel. A heavy, eyes-down "
        "head and long level body come before propulsion. If hips or legs sink, "
        "frame it as drag and prescribe a balance focal point such as Superman "
        "Glide / eyes down rather than more effort."
    ),
    "head_breath": (
        "HEAD/BREATH TI FOCUS: The head should stay relaxed and aligned with the "
        "spine. Breathing should come from body rotation, not lifting the chin. "
        "Use cues like weightless head, one-goggle breath, and keep the head on "
        "the pillow. Never infer breathing rhythm from one visible breath."
    ),
    "recovery_elbow": (
        "RECOVERY TI FOCUS: The recovering arm should rest. Look for elbow-led, "
        "soft-hand recovery: shark-fin elbow, floppy hand, quiet shoulder. "
        "Fingertip-drag is a drill cue, not a requirement that every normal stroke "
        "literally drag the fingers. If the arm is tense, straight, dropped, or "
        "wide, prescribe Shark Fin / Zipper / fingertip-drag style focal points."
    ),
    "entry_reach": (
        "ENTRY/REACH TI FOCUS: Favor a clean mail-slot entry and patient lead "
        "hand that lengthens the vessel into a skate line. Ideally one arm stays "
        "forward as the other recovers; the lead hand should not drop too early. "
        "From side-on footage, judge reach relative to the head/shoulder only. "
        "Never call crossover unless the camera angle actually supports "
        "cross-midline judgment."
    ),
    "body_rotation": (
        "ROTATION TI FOCUS: Rotation should be core-led: hip and shoulder move "
        "together as the body skates from side to side. Limited roll is a balance "
        "and drag issue before it is a power issue. Prescribe Zen Skate or Switch "
        "drills when rotation is the visible problem."
    ),
    "catch_pull": (
        "CATCH/PULL TI FOCUS: The catch is an underwater anchoring skill. From "
        "above-water footage, show an honest unavailable card rather than "
        "guessing. Ask for underwater footage or in-person coaching before "
        "commenting on high-elbow catch or pull path."
    ),
    "kick": (
        "KICK TI FOCUS: For efficiency-first freestyle, the kick is rhythm and "
        "rotation support, not brute force. A compact 2-beat kick often fits "
        "distance/efficiency work, but sprinting may use more kick. Do not judge "
        "kick timing or 2-beat versus 6-beat rhythm unless the footage truly shows "
        "it."
    ),
}

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


def build_ti_block(aspect: Optional[str] = None) -> str:
    """Shared TI-informed doctrine appended to coach prompts.

    ``aspect`` can be an AREA_LABELS / grade key such as ``body_line`` or
    ``head_breath``. Unknown aspect returns the core block only.
    """
    if aspect and aspect in _TI_ASPECT_BLOCKS:
        return f"{TI_CORE_BLOCK}\n{_TI_ASPECT_BLOCKS[aspect]}"
    return TI_CORE_BLOCK


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
