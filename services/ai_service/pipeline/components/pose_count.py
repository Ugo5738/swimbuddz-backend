"""Stage-1 component — the DETERMINISTIC recovery counter (yolov8-pose).

The VLM phase-segment count (``collate`` over ``ctx.instances``) is only ~53%
within-±1 on the golden set — AI vision stroke-counting is unreliable. This
component counts recoveries from the near-arm WRIST trajectory instead
(``coach.pose.count_recoveries``), which validated at ±1–2 on good-detection
side-on laps (MAE 0.67), and — crucially — REFUSES a count when pose detection
is too sparse to trust (the per-stroke drilldown is then suppressed).

It does ONE thing: run the pose counter over the clip's own densely-decoded
frames and stash the ``RecoveryResult`` on ``ctx.pose_recovery`` for ``collate``
to prefer over the VLM count. No findings, no coaching here.

Runs only after the gate (a REFUSE short-circuits the pipeline before any CPU is
spent here) and only when ``STROKELAB_COACH_POSE_COUNT`` is on. Heavy deps
(cv2/torch/ultralytics) stay LAZY — this module imports without them, and the
work runs in a thread so the event loop isn't blocked. ``count_fn`` is injectable
so the wiring is unit-testable with no model.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional

from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.types import (
    ComponentResult,
    Granularity,
    InputProfile,
    Phase,
    RunContext,
)

# ctx -> RecoveryResult | None  (None when there's no clip/frames to count)
CountFn = Callable[[RunContext], Awaitable[object]]


def _decode_dense(
    path: str, stride: int = 2, max_frames: int = 300, long_edge: int = 720
):
    """Strided BGR frame decode + timestamps (mirrors the validated eval decode)."""
    import math

    import cv2

    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total > 0:
        stride = max(stride, math.ceil(total / max_frames))
    frames, times, idx = [], [], 0
    while True:
        if not cap.grab():
            break
        if idx % stride == 0:
            ok, img = cap.retrieve()
            if ok and img is not None:
                h, w = img.shape[:2]
                s = long_edge / max(h, w)
                if s < 1:
                    img = cv2.resize(img, (int(w * s), int(h * s)))
                frames.append(img)
                times.append(idx / fps)
        idx += 1
    cap.release()
    return frames, times


async def _default_count(ctx: RunContext):
    """Decode the clip's own dense frames and run the pose counter (in a thread)."""
    path = ctx.video_path
    if not path:
        return None

    def _work():
        from services.ai_service.coach.pose import count_recoveries  # lazy: torch

        frames, times = _decode_dense(path)
        if not frames:
            return None
        return count_recoveries(frames, times)

    return await asyncio.to_thread(_work)


def _payload(result) -> dict:
    """JSON-safe view of a RecoveryResult — stored on ctx + in the cache."""
    return {
        "count": result.count,
        "confidence": result.confidence,
        "detection_rate": round(result.detection_rate, 3),
        "near_wrist_conf": round(result.near_wrist_conf, 3),
        "refused": result.refused,
    }


class PoseCountComponent(Component):
    name = "pose_count"
    consumes = Phase.CLIP
    granularity = Granularity.CHUNK
    profiles = (InputProfile.SIDE_ON_ABOVE, InputProfile.UNKNOWN)

    def __init__(self, count_fn: Optional[CountFn] = None):
        self._count_fn = count_fn

    async def run(self, ctx: RunContext) -> ComponentResult:
        start = time.monotonic()

        # $0 replay — the count is deterministic but costs CPU, so reuse the cache.
        cache = ctx.cache
        if cache is not None and "pose_recovery" in cache:
            ctx.pose_recovery = cache["pose_recovery"]
            return ComponentResult(
                self.name,
                [],
                latency_ms=int((time.monotonic() - start) * 1000),
                meta={"replayed": True, **(ctx.pose_recovery or {})},
            )

        count_fn = self._count_fn or _default_count
        result = await count_fn(ctx)
        if result is None:
            ctx.pose_recovery = None
            return ComponentResult(
                self.name,
                [],
                latency_ms=int((time.monotonic() - start) * 1000),
                meta={"available": False},
            )

        payload = _payload(result)
        ctx.pose_recovery = payload
        if cache is not None:
            cache["pose_recovery"] = payload

        # No user-facing finding — collate turns this into the count/summary.
        return ComponentResult(
            self.name,
            [],
            latency_ms=int((time.monotonic() - start) * 1000),
            meta=payload,
        )
