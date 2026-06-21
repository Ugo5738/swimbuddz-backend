"""Gate component — the 3-tier view/usability decision.

Wraps the proven, vote-stabilised ``coach.run_gate`` and maps its verdict to a
``GateTier`` (clean / borderline / refuse). The runner consults this first.

NOTE: the current ``GATE_SYSTEM_PROMPT`` is still BINARY (side-on / not), so this
mapping derives tiers from the binary verdict + vote agreement. Borderline-angled
clips are under-detected until the gate is upgraded to emit a graded
``profile_quality`` (design doc §4, §10). The tier→behaviour contract here does
not change when that lands — only ``_tier`` does.
"""

from __future__ import annotations

import time
from dataclasses import asdict

from services.ai_service.coach.coach import GateVerdict, run_gate
from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.types import (
    SEVERITY_INFO,
    ComponentResult,
    Finding,
    GateTier,
    Phase,
    RunContext,
)

# Views from which freestyle technique cannot be coached at all → hard refuse.
_REFUSE_VIEWS = {"underwater", "overhead", "head-on"}
_CONFIDENT = 0.6  # vote agreement needed to act on a "bad" verdict
_CLEAN = 0.67  # vote agreement needed to call an accept "clean"


def _tier(v: GateVerdict) -> GateTier:
    if v.n_valid == 0:  # couldn't read the clip at all
        return GateTier.REFUSE
    if v.stroke == "other" and v.agreement >= _CONFIDENT:
        return GateTier.REFUSE
    if v.view in _REFUSE_VIEWS and v.agreement >= _CONFIDENT:
        return GateTier.REFUSE
    if v.usable and v.agreement >= _CLEAN:
        return GateTier.CLEAN
    return GateTier.BORDERLINE  # angled / split-vote / uncertain → coach with a nudge


class GateComponent(Component):
    name = "gate"
    IS_GATE = True
    consumes = Phase.CLIP

    async def run(self, ctx: RunContext) -> ComponentResult:
        start = time.monotonic()
        cache = ctx.cache
        if cache is not None and "gate" in cache:
            verdict = GateVerdict(**cache["gate"])  # replay — no API
            paid = 0.0
        else:
            verdict = await run_gate(
                ctx.frames,
                model=ctx.config.gate_model,
                n_votes=ctx.config.gate_votes,
                image_detail=ctx.config.gate_detail,
                stroke_hint=ctx.stroke_hint,
            )
            paid = verdict.cost_usd
            if cache is not None:
                cache["gate"] = asdict(verdict)
        tier = _tier(verdict)
        finding = Finding(
            component=self.name,
            observation=(
                f"Gate: {tier.value} — view={verdict.view}, stroke={verdict.stroke}, "
                f"{verdict.n_valid}/{verdict.n_votes} votes agree "
                f"({verdict.agreement:.0%}); swimmers={verdict.swimmer_count}"
            ),
            severity=SEVERITY_INFO,
            confidence=verdict.agreement,
            extra={"view": verdict.view, "stroke": verdict.stroke},
        )
        return ComponentResult(
            component=self.name,
            findings=[finding],
            cost_usd=paid,  # 0 on a cache replay
            latency_ms=int((time.monotonic() - start) * 1000),
            meta={"tier": tier, "verdict": verdict},
        )
