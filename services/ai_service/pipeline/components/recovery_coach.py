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
from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    SEVERITY_INFO,
    SEVERITY_STRENGTH,
    ComponentResult,
    Finding,
    Granularity,
    Instance,
    Phase,
    RunContext,
)

# How "good" each over-water elbow shape is (for the consistency/fatigue read).
_ELBOW_SCORE = {"high": 2, "wide": 1, "dropped": 0}

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

    async def run(self, ctx: RunContext) -> ComponentResult:
        # Coach the representative recoveries (the base), then add ONE honest
        # across-strokes consistency/fatigue read derived from those per-instance
        # verdicts (deterministic, $0).
        res = await super().run(ctx)
        agg = self._consistency(res.findings)
        if agg is not None:
            res.findings.append(agg)
            res.meta["consistency"] = agg.extra.get("trend")
        return res

    def _consistency(self, findings) -> Finding | None:
        """An honest consistency/fatigue read. Needs ≥2 CLEAR recovery reads
        (so it only fires when ``max_coached_recoveries`` > 1) and only claims
        fatigue when the late strokes are genuinely worse than the early ones —
        never a fatigue read we can't actually see."""
        reads = sorted(
            (f.instance_id, f.extra.get("elbow"))
            for f in findings
            if isinstance(f.instance_id, int) and f.extra.get("elbow") in _ELBOW_SCORE
        )
        if len(reads) < 2:
            return None  # not enough clear reads to compare — say nothing
        early, late = _ELBOW_SCORE[reads[0][1]], _ELBOW_SCORE[reads[-1][1]]
        if late < early:
            obs = (
                "Your recovery is cleaner early on — the elbow drops on your later "
                "strokes. That's fatigue creeping in; train holding the high-elbow "
                "shape when tired."
            )
            sev, trend = SEVERITY_FIX, "declining"
        elif all(e == "high" for _, e in reads):
            obs = (
                "Your recovery held its high-elbow shape consistently across the clip "
                "— nicely repeatable."
            )
            sev, trend = SEVERITY_STRENGTH, "strong"
        elif late > early:
            obs = (
                "Your recovery tightened up as you went — better on the later strokes."
            )
            sev, trend = SEVERITY_INFO, "improving"
        else:
            obs = "Your recovery looked fairly consistent across the clip."
            sev, trend = SEVERITY_INFO, "steady"
        return Finding(
            component=self.name,
            observation=obs,
            severity=sev,
            confidence=0.5,  # a derived read from a few samples — hedged
            area="consistency",
            extra={
                "aggregate": True,
                "trend": trend,
                "reads": [{"instance_id": i, "elbow": e} for i, e in reads],
            },
        )

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
