"""Stage-3 component — collate the segmented instances into counts/metrics.

This is where counting lives (NOT in the segment stage). It reads the per-phase,
per-arm ``Instance`` chunks Stage-1 produced on ``ctx.instances`` and derives:
  - the hedged "~N recoveries" summary (near-arm count == stroke_cycles, 1:1);
  - per-phase chunk counts (recovery/entry/glide/breath) for analytics.

Deterministic and free — no VLM call. The count is approximate (golden-set
within-±1 is ~53%), so the observation always says "~N", never a hard number.
"""

from __future__ import annotations

import time

from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.types import (
    SEVERITY_INFO,
    ComponentResult,
    Finding,
    Granularity,
    InputProfile,
    Phase,
    RunContext,
)


class CollateComponent(Component):
    name = "collate"
    consumes = Phase.CLIP
    granularity = Granularity.CHUNK
    profiles = (InputProfile.SIDE_ON_ABOVE, InputProfile.UNKNOWN)

    async def run(self, ctx: RunContext) -> ComponentResult:
        start = time.monotonic()
        instances = ctx.instances or []

        phase_counts: dict[str, int] = {}
        for inst in instances:
            phase_counts[inst.phase.value] = phase_counts.get(inst.phase.value, 0) + 1

        recs = [i for i in instances if i.phase == Phase.RECOVERY]
        near = sum(1 for i in recs if i.arm == "near")
        far = sum(1 for i in recs if i.arm == "far")
        n = near or far  # near-arm is the 1:1 convention; fall back to far if no near

        obs = (
            f"Detected ~{n} {'recovery' if n == 1 else 'recoveries'} (approximate)."
            if n
            else "No clear recoveries detected in this clip."
        )
        finding = Finding(
            component=self.name,
            observation=obs,
            severity=SEVERITY_INFO,
            confidence=0.5,  # hedged: count accuracy ~53% within ±1 on the golden set
            area="recovery_elbow",
            extra={
                "recovery_count_hedged": n,
                "near_arm_recoveries": near,
                "far_arm_recoveries": far,
                "phase_counts": phase_counts,
                "recovery_windows": [
                    [round(i.start_s, 2), round(i.end_s, 2)] for i in recs
                ],
            },
        )
        return ComponentResult(
            component=self.name,
            findings=[finding],
            latency_ms=int((time.monotonic() - start) * 1000),
            meta={"phase_counts": phase_counts},
        )
