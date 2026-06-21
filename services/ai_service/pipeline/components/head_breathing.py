"""Stage-2 — coach head carriage + breathing side.

Prefers a BREATH instance (to read the breath + which side); with no breath in the
clip it falls back to a GLIDE frame and reports the resting head only. One VLM call
yields up to TWO findings: head carriage (neutral/lifted → grade) and the breath
SIDE (an observation, never a fault). Honesty: breath side is reported ONLY when a
frame actually shows the head turned mid-breath, and the prompt forbids any
breathing rhythm / frequency / count.
"""

from __future__ import annotations

from services.ai_service.pipeline.components.aspect import AspectCoachComponent
from services.ai_service.pipeline.types import (
    Granularity,
    Instance,
    Phase,
    RunContext,
)

HEAD_BREATH_PROMPT = """\
You are a freestyle coach. These still frames show a freestyle swimmer's head. \
Judge the HEAD CARRIAGE: is the head NEUTRAL (face down, looking toward the bottom, \
the waterline around the crown) or LIFTED (looking forward/up — which sinks the \
legs)? If a frame clearly shows the head turned to the SIDE to take a breath, also \
report which side. Do NOT report breathing rhythm, frequency, or how OFTEN they \
breathe — only the head position and, if you can see it, the side of a breath. If \
the head isn't clearly visible, say "unclear". \
Return ONLY this JSON: {"head": "neutral" | "lifted" | "unclear", "breath_side": \
"left" | "right" | "both" | "none_seen", "note": "<one short plain sentence>", \
"confidence": 0.0-1.0}"""


class HeadBreathingComponent(AspectCoachComponent):
    name = "head_breathing"
    aspect = "head_breath"  # the AREA_LABELS / grade key
    consumes = Phase.BREATH
    granularity = Granularity.FRAME
    image_detail = "low"
    max_tokens = 300
    SYSTEM_PROMPT = HEAD_BREATH_PROMPT

    def _instances(self, ctx: RunContext):
        # Prefer a breath frame (head + side); fall back to a glide frame for the
        # resting head only.
        breaths = [i for i in ctx.instances if i.phase == Phase.BREATH]
        if breaths:
            return breaths
        return [i for i in ctx.instances if i.phase == Phase.GLIDE]

    def _findings(self, parsed: dict, inst: Instance, window, ctx: RunContext):
        conf = float(parsed.get("confidence", 0.0) or 0.0)
        head = str(parsed.get("head", "unclear"))
        note = parsed.get("note", "")
        findings = [
            self._mk(
                head, note, inst, window, ctx, conf=conf, extra_payload={"kind": "head"}
            )
        ]
        # Breath side is only meaningful on an actual breath frame, and only when a
        # side was seen — an observation, never a fault (grade keeps it INFO).
        side = (
            str(parsed.get("breath_side", "none_seen"))
            if inst.phase == Phase.BREATH
            else "none_seen"
        )
        if side in ("left", "right", "both"):
            side_note = (
                f"Breathing to the {side}."
                if side != "both"
                else "Breathing both sides."
            )
            findings.append(
                self._mk(
                    side,
                    side_note,
                    inst,
                    window,
                    ctx,
                    conf=conf,
                    extra_payload={"kind": "breath_side"},
                )
            )
        return findings
