"""Gate component — the graded 3-tier view/usability decision.

Wraps the vote-stabilised ``coach.run_gate`` and maps its verdict (including
graded ``profile_quality`` and ``catastrophic_flags`` when present) to a
``GateTier`` (clean / borderline / refuse). Backwards-compatible with legacy
binary cached gate verdicts where ``profile_quality`` is empty.
"""

from __future__ import annotations

import time
from dataclasses import asdict, fields

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

# Views from which above-water freestyle technique cannot be coached at all.
# Overhead/elevated footage can still show recovery/head/rotation, so coach it as
# borderline rather than refusing it outright.
_REFUSE_VIEWS = {"underwater", "head-on"}
_REFUSE_FLAGS = {"non_freestyle", "underwater", "head_on", "no_swimmer", "unreadable"}
_CONFIDENT = 0.6  # vote agreement needed to act on a "bad" verdict
_CLEAN = 2 / 3  # vote agreement needed to call an accept "clean"
_GATE_VERDICT_KEYS = {f.name for f in fields(GateVerdict)}


def _tier(v: GateVerdict) -> GateTier:
    if v.n_valid == 0:  # couldn't read the clip at all
        return GateTier.REFUSE
    flags = {str(f).strip().lower() for f in (v.catastrophic_flags or [])}
    if flags & _REFUSE_FLAGS and v.agreement >= _CONFIDENT:
        return GateTier.REFUSE
    if v.stroke == "other" and v.agreement >= _CONFIDENT:
        return GateTier.REFUSE
    if v.view in _REFUSE_VIEWS and v.agreement >= _CONFIDENT:
        return GateTier.REFUSE
    pq = (v.profile_quality or "").strip().lower()
    if pq == "poor" and not v.usable and v.agreement >= _CONFIDENT:
        return GateTier.REFUSE
    if not v.usable and v.swimmer_count == 0 and v.agreement >= _CONFIDENT:
        return GateTier.REFUSE
    if v.usable and v.view == "side-on" and v.agreement >= _CLEAN:
        if pq == "partial":
            return GateTier.BORDERLINE
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
            cached_gate = {
                k: v for k, v in cache["gate"].items() if k in _GATE_VERDICT_KEYS
            }
            verdict = GateVerdict(**cached_gate)  # replay — no API
            paid = 0.0
        else:
            verdict = await run_gate(
                ctx.frames,
                model=ctx.config.gate_model,
                n_votes=ctx.config.gate_votes,
                image_detail=ctx.config.gate_detail,
                stroke_hint=ctx.stroke_hint,
                reasoning_effort=ctx.config.coach_reasoning_effort,
            )
            paid = verdict.cost_usd
            if cache is not None:
                cache["gate"] = asdict(verdict)
        tier = _tier(verdict)
        pq_note = (
            f", profile={verdict.profile_quality}" if verdict.profile_quality else ""
        )
        finding = Finding(
            component=self.name,
            observation=(
                f"Gate: {tier.value} — view={verdict.view}, stroke={verdict.stroke}"
                f"{pq_note}, {verdict.n_valid}/{verdict.n_votes} votes agree "
                f"({verdict.agreement:.0%}); swimmers={verdict.swimmer_count}"
            ),
            severity=SEVERITY_INFO,
            confidence=verdict.agreement,
            extra={
                "view": verdict.view,
                "stroke": verdict.stroke,
                "profile_quality": verdict.profile_quality or None,
                "catastrophic_flags": list(verdict.catastrophic_flags or []),
            },
        )
        return ComponentResult(
            component=self.name,
            findings=[finding],
            cost_usd=paid,  # 0 on a cache replay
            latency_ms=int((time.monotonic() - start) * 1000),
            meta={"tier": tier, "verdict": verdict},
        )
