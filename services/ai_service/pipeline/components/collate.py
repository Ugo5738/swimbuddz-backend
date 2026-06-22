"""Stage-3 component — collate the segmented instances into counts/metrics.

This is where counting lives (NOT in the segment stage). It reads the per-phase,
per-arm ``Instance`` chunks Stage-1 produced on ``ctx.instances`` and derives:
  - the hedged "~N recoveries" summary (near-arm count == stroke_cycles, 1:1);
  - per-phase chunk counts (recovery/entry/glide/breath) for analytics.

The recovery COUNT prefers the deterministic pose counter (``ctx.pose_recovery``,
set by the pose_count component) over the VLM instance count when present: pose is
±1–2 on good-detection laps vs the VLM's ~53% within-±1. When the pose detection
gate REFUSED (``refused``), the count is set to ``None`` so the count card + the
per-stroke drilldown are suppressed (the frontend keys on a numeric count) — we
don't show a number we can't trust. With no pose result we fall back to the VLM
instance count (legacy behaviour, unchanged). Deterministic; no VLM call here.
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
        vlm_n = (
            near or far
        )  # near-arm is the 1:1 convention; fall back to far if no near

        # Prefer the deterministic pose count; refuse → no number; else VLM fallback.
        pose = ctx.pose_recovery
        if pose is not None and pose.get("refused"):
            n = None  # detection gate refused — suppress the count + drilldown
            confidence = 0.2
            source = "pose_low_detection"
            obs = (
                "Couldn't reliably count recoveries from this footage — the swimmer "
                "wasn't clearly visible enough to trust a stroke count."
            )
        elif pose is not None and pose.get("count") is not None:
            n = pose["count"]
            confidence = 0.8  # deterministic pose count, ±1–2 on good-detection laps
            source = "pose"
            obs = (
                f"Detected ~{n} {'recovery' if n == 1 else 'recoveries'} (approximate)."
                if n
                else "No clear recoveries detected in this clip."
            )
        else:
            n = vlm_n  # no pose result → legacy VLM instance count
            confidence = 0.5  # ~53% within ±1 on the golden set
            source = "vlm_instances"
            obs = (
                f"Detected ~{n} {'recovery' if n == 1 else 'recoveries'} (approximate)."
                if n
                else "No clear recoveries detected in this clip."
            )

        finding = Finding(
            component=self.name,
            observation=obs,
            severity=SEVERITY_INFO,
            confidence=confidence,
            area="recovery_elbow",
            extra={
                # numeric ⇒ count card + per-stroke drilldown render; None ⇒ suppressed
                "recovery_count_hedged": n,
                "count_source": source,
                "pose_detection_rate": (pose or {}).get("detection_rate"),
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
