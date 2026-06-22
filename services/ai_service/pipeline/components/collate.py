"""Stage-3 component — collate the segmented instances into counts/metrics.

This is where counting lives (NOT in the segment stage). It reads the per-phase,
per-arm ``Instance`` chunks Stage-1 produced on ``ctx.instances`` and derives:
  - the hedged "~N recoveries" summary (near-arm count == stroke_cycles, 1:1);
  - per-phase chunk counts (recovery/entry/glide/breath) for analytics.

The recovery count is just ``len(near-arm recovery instances)`` — and those
instances are pose-segmented when pose_count ran (±1–2 on good-detection laps vs
the VLM's ~53% within-±1), so the count matches the per-stroke drilldown (both
read the one instances layer). When the pose detection gate REFUSED, pose_count
already dropped the near-arm recovery rows AND we null the count here, so the
count card + drilldown both hide (the frontend keys on a numeric count) — we don't
show a number we can't trust. With no pose result the VLM instances stand (legacy
behaviour). Deterministic; no VLM call here. Derived reads (fatigue/consistency
trends) belong in this layer too — see recovery_coach._consistency for the first.
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

        # Count the instances — which are pose-segmented when pose_count ran (the
        # same rows the drilldown drills, so count and drilldown agree). On a pose
        # REFUSE the recovery rows were dropped AND we null the count so the count
        # card + the per-stroke drilldown both hide. No pose ⇒ legacy VLM count.
        pose = ctx.pose_recovery
        if pose is not None and pose.get("refused"):
            n = None  # detection gate refused — suppress the count + drilldown
            confidence = 0.2
            source = "pose_low_detection"
            obs = (
                "Couldn't reliably count recoveries from this footage — the swimmer "
                "wasn't clearly visible enough to trust a stroke count."
            )
        else:
            n = near or far  # near-arm is the 1:1 convention; fall back to far
            confidence = 0.8 if pose is not None else 0.5  # pose ±1–2 vs VLM ~53%
            source = "pose" if pose is not None else "vlm_instances"
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
