"""Stage-1 component — classify every frame and segment ALL visible phases.

Wraps the validated VLM-classify (``coach.classify.classify_strip``) → deterministic
group (``pipeline.segment.group_phase_instances``) path as a pipeline component.

It does TWO things and nothing more (the plug-and-play contract):
  1. classify every frame (one VLM call; labels carry phase + arm + recovery
     sub-phase) and STORE every label — nothing is discarded (the data-loss rule);
  2. group the labels into per-phase, per-arm ``Instance`` chunks and set
     ``ctx.instances`` (consumed by the per-instance coaches and the collate stage).

There is NO counting and NO coaching here — counts/metrics live in the collate
component, qualitative feedback in the coach components. ``classify_fn`` is
injectable so the component is unit-testable with NO API.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Awaitable, Callable, Optional

from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.segment import FrameLabel, group_phase_instances
from services.ai_service.pipeline.types import (
    ComponentResult,
    Granularity,
    InputProfile,
    Phase,
    RunContext,
)

# (frames, model, image_detail, batch) -> (list[FrameLabel], cost_usd)
ClassifyFn = Callable[..., Awaitable[tuple[list, float]]]


async def _default_classify(strip, **kwargs):
    from services.ai_service.coach.classify import classify_strip  # lazy: needs litellm

    return await classify_strip(strip, **kwargs)


def _instance_dict(inst) -> dict:
    """JSON-safe view of an Instance — stored in the cache + the labels table feed."""
    return {
        "phase": inst.phase.value,
        "instance_id": inst.instance_id,
        "arm": inst.arm,
        "start_s": round(inst.start_s, 3),
        "end_s": round(inst.end_s, 3),
        "peak_s": round(inst.peak_s, 3),
        "peak_index": inst.peak_index,
        "confidence": inst.confidence,
    }


class PhaseSegmentComponent(Component):
    name = "phase_segment"
    consumes = Phase.CLIP
    granularity = Granularity.CHUNK
    profiles = (InputProfile.SIDE_ON_ABOVE, InputProfile.UNKNOWN)

    def __init__(self, classify_fn: Optional[ClassifyFn] = None):
        self._classify_fn = classify_fn

    async def run(self, ctx: RunContext) -> ComponentResult:
        start = time.monotonic()
        strip = ctx.strip or ctx.frames  # fall back to the key frames if no dense strip
        if not strip:
            return ComponentResult(
                self.name,
                [],
                latency_ms=int((time.monotonic() - start) * 1000),
                meta={"n_instances": 0, "by_phase": {}},
            )

        cache = ctx.cache
        if cache is not None and len(cache.get("labels", [])) == len(strip):
            labels = [FrameLabel(**d) for d in cache["labels"]]  # replay — no API
            cost = 0.0
        else:
            classify_fn = self._classify_fn or _default_classify
            labels, cost = await classify_fn(
                strip,
                model=ctx.config.segment_model,
                image_detail=ctx.config.segment_detail,
                batch=ctx.config.segment_batch,
            )
            if cache is not None:
                cache["labels"] = [asdict(lab) for lab in labels]

        # Group EVERY visible phase into chunks (recovery per arm). No phase is
        # reclassified away; the far arm is kept as its own chunks.
        instances = group_phase_instances(labels, [f.timestamp_s for f in strip])
        ctx.instances = instances  # hand off to Stage-2 coaches + the collate stage

        if cache is not None:
            cache["instances"] = [_instance_dict(i) for i in instances]

        by_phase: dict[str, int] = {}
        for inst in instances:
            by_phase[inst.phase.value] = by_phase.get(inst.phase.value, 0) + 1

        # Segment ONLY segments — it emits no user-facing findings. The collate
        # component turns these instances into the hedged count/summary.
        return ComponentResult(
            component=self.name,
            findings=[],
            cost_usd=cost,
            latency_ms=int((time.monotonic() - start) * 1000),
            meta={"n_instances": len(instances), "by_phase": by_phase},
        )
