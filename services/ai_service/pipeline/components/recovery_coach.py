"""Stage-2 per-instance component — coach a representative recovery.

Consumes the RECOVERY instances Stage-1 put on ``ctx.instances``, picks a
representative one (not all — that's cost ×N for little gain; the UX coaches
others on demand), gathers the strip frames inside that instance's [start,end]
window, and asks a focused recovery prompt. Emits an instance-tagged Finding so
the UX can show "recovery #k (t=…): …". Absent recoveries → zero findings (honest,
not a bluff).

``coach_fn`` is injectable for no-API unit testing.
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable, Optional

from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    SEVERITY_INFO,
    ComponentResult,
    Finding,
    FrameRef,
    Granularity,
    InputProfile,
    Phase,
    RunContext,
)

RECOVERY_PROMPT = """\
You are a freestyle coach. These still frames, in time order, show ONE over-water \
arm recovery (the arm swinging forward above the water). Comment ONLY on this \
recovery — nothing else. Is the elbow HIGH (leading, above the hand) or DROPPED/ \
LOW or swung WIDE out to the side? If the frames don't show it clearly, say so. \
Return ONLY this JSON: {"assessment": "<one short plain sentence>", "elbow": \
"high" | "dropped" | "wide" | "unclear", "confidence": 0.0-1.0}"""

# (frames, model) -> (parsed_dict, cost_usd)
CoachFn = Callable[..., Awaitable[tuple[dict, float]]]


async def _default_recovery_coach(frames, *, model=None):
    from services.ai_service.providers.base import call_vlm  # lazy: needs litellm

    resp = await call_vlm(
        system_prompt=RECOVERY_PROMPT,
        user_prompt="Assess this single arm recovery and return only the JSON.",
        images=[f.jpeg for f in frames],
        model=model,
        image_detail="auto",
        max_tokens=400,
        response_format={"type": "json_object"},
        trace_name="strokelab_recovery",
    )
    try:
        return resp.parse_json(), resp.cost_usd
    except Exception:
        return {"assessment": "", "elbow": "unclear", "confidence": 0.0}, resp.cost_usd


def _representatives(instances, k: int):
    """Pick up to k evenly-spaced instances (k=1 → the middle one)."""
    if k >= len(instances):
        return instances
    if k == 1:
        return [instances[len(instances) // 2]]
    step = (len(instances) - 1) / (k - 1)
    return [instances[round(i * step)] for i in range(k)]


class RecoveryCoachComponent(Component):
    name = "recovery_coach"
    consumes = Phase.RECOVERY
    granularity = Granularity.CHUNK
    profiles = (InputProfile.SIDE_ON_ABOVE, InputProfile.UNKNOWN)

    def __init__(self, coach_fn: Optional[CoachFn] = None):
        self._coach_fn = coach_fn

    async def run(self, ctx: RunContext) -> ComponentResult:
        start = time.monotonic()
        # Coach the near (camera-facing) arm — most reliably visible side-on. Fall
        # back to all recoveries only if no near-arm recovery was detected.
        recs = [
            i for i in ctx.instances if i.phase == Phase.RECOVERY and i.arm == "near"
        ]
        if not recs:
            recs = [i for i in ctx.instances if i.phase == Phase.RECOVERY]
        if not recs:
            return ComponentResult(self.name, [])  # nothing to coach — honest zero
        strip = ctx.strip or ctx.frames
        coach_fn = self._coach_fn or _default_recovery_coach

        cache = ctx.cache
        findings: list[Finding] = []
        cost = 0.0
        for inst in _representatives(recs, ctx.config.max_coached_recoveries):
            window = [f for f in strip if inst.start_s <= f.timestamp_s <= inst.end_s]
            if not window:  # fall back to the frame nearest the peak
                window = [min(strip, key=lambda f: abs(f.timestamp_s - inst.peak_s))]
            key = f"recovery:{inst.instance_id}"
            if cache is not None and key in cache:
                data, c = cache[key], 0.0  # replay — no API
            else:
                data, c = await coach_fn(window[:4], model=ctx.config.coach_model)
                if cache is not None:
                    cache[key] = data
            cost += c
            elbow = data.get("elbow", "unclear")
            findings.append(
                Finding(
                    component=self.name,
                    observation=data.get("assessment", "")
                    or f"Recovery #{inst.instance_id}",
                    severity=SEVERITY_FIX
                    if elbow in ("dropped", "wide")
                    else SEVERITY_INFO,
                    evidence_frames=[
                        FrameRef(f.index, f.timestamp_s) for f in window[:3]
                    ],
                    confidence=float(data.get("confidence", 0.0) or 0.0),
                    instance_id=inst.instance_id,
                    area="recovery_elbow",
                    extra={"elbow": elbow, "t": round(inst.peak_s, 2)},
                )
            )
        return ComponentResult(
            self.name,
            findings,
            cost_usd=cost,
            latency_ms=int((time.monotonic() - start) * 1000),
            meta={"coached": len(findings), "available_instances": len(recs)},
        )
