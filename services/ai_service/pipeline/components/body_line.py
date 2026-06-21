"""Stage-2 — coach body line (head/hip/leg sink) from a glide frame.

The single biggest above-water signal and the most discipline-neutral fault (a
sinking hip is drag for everyone — distance just ranks it top). Consumes a GLIDE
instance (arms in/near the water, no over-water arm) so the read isn't corrupted
by a transient breath-lift, and judges ONE representative frame. ``grade()`` then
prioritises it by discipline. Honesty gate: if the waterline-vs-body can't be
seen, the model returns ``unclear`` → INFO, no fault.
"""

from __future__ import annotations

from services.ai_service.pipeline.components.aspect import AspectCoachComponent
from services.ai_service.pipeline.types import Granularity, Instance, Phase

BODY_LINE_PROMPT = """\
You are a freestyle coach. These still frames show a swimmer GLIDING (no arm over \
the water). Judge ONLY the BODY LINE — how level the body sits, head to feet. \
Look for: the head LIFTED/looking-forward vs neutral/looking-down, the HIPS \
riding low, the LEGS sinking, a PIKE (bend) at the hips, or an over-ARCHED back. \
IGNORE any frame where the head is turned to the side to breathe — a breath \
briefly lifts the hips and would fool you. If you cannot see the waterline \
cutting across the SIDE of the body, say "unclear" — do not guess. \
Return ONLY this JSON: {"body_line": "flat" | "hips_low" | "legs_low" | "piked" \
| "arched" | "unclear", "note": "<one short plain sentence>", \
"confidence": 0.0-1.0}"""


class BodyLineComponent(AspectCoachComponent):
    name = "body_line"
    aspect = "body_line"
    consumes = Phase.GLIDE
    granularity = Granularity.FRAME  # one resting glide frame
    image_detail = "low"
    max_tokens = 300
    SYSTEM_PROMPT = BODY_LINE_PROMPT

    def _findings(self, parsed: dict, inst: Instance, window, ctx):
        verdict = str(parsed.get("body_line", "unclear"))
        note = parsed.get("note", "")
        conf = float(parsed.get("confidence", 0.0) or 0.0)
        return [self._mk(verdict, note, inst, window, ctx, conf=conf)]
