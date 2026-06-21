"""Stage-2 — coach hand entry & front extension from an entry frame.

The hardest aspect to see side-on, so it is the most conservative: crossover is
STRUCTURALLY excluded (no enum value + an explicit in-prompt ban — a cross-midline
entry is a top-down fault, NOT judgeable from the side; it's what got gpt-5-mini
rejected). Confidence is capped because entry is only partly visible. The headline
goal flip lives here: a long held extension is a STRENGTH for distance (free reach)
but, for a sprinter, only a HEDGED dead-spot caution (grade → INFO, conf ≤ 0.5) —
never a hard fix, because a pause lives between frames.
"""

from __future__ import annotations

from services.ai_service.pipeline.components.aspect import AspectCoachComponent
from services.ai_service.pipeline.types import Granularity, Instance, Phase, RunContext

ENTRY_PROMPT = """\
You are a freestyle coach. These still frames show a freestyle hand ENTERING the \
water in front of the head and the lead arm reaching forward. Judge ONLY the hand \
entry and front extension. Is the reach CLEAN and EXTENDED (arm reaching well \
forward, roughly in line with the shoulder), SHORT (entering close to the head \
with little reach), or an OVERREACH (reaching out past the shoulder line)? \
You are looking from the SIDE: you CANNOT see whether the hand crosses the body's \
midline — NEVER report a "crossover" or "cross-midline" entry, it is not visible \
from this angle. If you cannot see the entry clearly, say "unclear". \
Return ONLY this JSON: {"entry": "clean_extended" | "short" | "overreach" | \
"unclear", "note": "<one short plain sentence>", "confidence": 0.0-1.0}"""


class EntryReachComponent(AspectCoachComponent):
    name = "entry_reach"
    aspect = "entry_reach"
    consumes = Phase.ENTRY
    granularity = Granularity.FRAME
    image_detail = "low"
    max_tokens = 300
    SYSTEM_PROMPT = ENTRY_PROMPT

    def _findings(self, parsed: dict, inst: Instance, window, ctx: RunContext):
        verdict = str(parsed.get("entry", "unclear"))
        note = parsed.get("note", "")
        conf = min(float(parsed.get("confidence", 0.0) or 0.0), 0.7)  # partly visible
        # A long held reach for a SPRINTER is only a hedged dead-spot caution — cap
        # confidence and frame it that way; grade() already makes it INFO, not a fix.
        if ctx.coaching.discipline == "sprint" and verdict == "clean_extended":
            conf = min(conf, 0.5)
            note = note or (
                "Long front extension — for a sprint, make sure it isn't a dead-spot; "
                "start the catch a touch sooner."
            )
        return [self._mk(verdict, note, inst, window, ctx, conf=conf)]
