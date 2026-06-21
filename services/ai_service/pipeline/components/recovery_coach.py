"""Stage-2 — coach a representative near-arm recovery (the elbow).

Now an ``AspectCoachComponent`` subclass: it inherits selection/windowing/cache/
goal-block plumbing and only supplies the recovery prompt + the elbow→Finding
mapping. Behaviour-preserving (near-arm, one representative, instance-tagged), but
severity now flows through ``grade()`` so it's discipline-aware (e.g. a `wide`
elbow is a fix for distance but only info for a sprinter). Absent recoveries →
zero findings (honest). ``coach_fn`` is injectable for no-API tests.
"""

from __future__ import annotations

from services.ai_service.pipeline.components.aspect import AspectCoachComponent
from services.ai_service.pipeline.types import Granularity, Instance, Phase, RunContext

RECOVERY_PROMPT = """\
You are a freestyle coach. These still frames, in time order, show ONE over-water \
arm recovery (the arm swinging forward above the water). Comment ONLY on this \
recovery — nothing else. Is the elbow HIGH (leading, above the hand) or DROPPED/ \
LOW or swung WIDE out to the side? If the frames don't show it clearly, say so. \
Return ONLY this JSON: {"assessment": "<one short plain sentence>", "elbow": \
"high" | "dropped" | "wide" | "unclear", "confidence": 0.0-1.0}"""


class RecoveryCoachComponent(AspectCoachComponent):
    name = "recovery_coach"
    aspect = "recovery_elbow"
    consumes = Phase.RECOVERY
    arm = "near"  # camera-facing arm — most reliable side-on (fallback: far)
    granularity = Granularity.CHUNK
    image_detail = "auto"
    max_tokens = 400
    SYSTEM_PROMPT = RECOVERY_PROMPT

    def _rep_cap(self, ctx: RunContext) -> int:
        return ctx.config.max_coached_recoveries

    def _findings(self, parsed: dict, inst: Instance, window, ctx):
        elbow = str(parsed.get("elbow", "unclear"))
        note = parsed.get("assessment", "") or f"Recovery #{inst.instance_id}"
        conf = float(parsed.get("confidence", 0.0) or 0.0)
        return [
            self._mk(
                elbow,
                note,
                inst,
                window,
                ctx,
                conf=conf,
                extra_payload={"elbow": elbow, "t": round(inst.peak_s, 2)},
            )
        ]
