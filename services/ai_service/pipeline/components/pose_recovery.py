"""Stage-1 component — the DETERMINISTIC recovery SEGMENTER (yolov8-pose).

The VLM phase-segment is only ~53% within-±1 on the golden set — AI vision
stroke-counting is unreliable. This component segments near-arm recoveries from
the WRIST trajectory instead (``coach.pose.count_recoveries``), validated at ±1–2
on good-detection side-on laps (MAE 0.67), and REFUSES when pose detection is too
sparse to trust.

It owns the near-arm RECOVERY rows of the instances layer: it runs the pose
counter over the clip's own densely-decoded frames and REPLACES the near-arm
recovery ``Instance``s on ``ctx.instances`` with pose-derived ones (one per peak,
windowed by absolute time — the aspect coaches select strip frames by timestamp,
so pose's own frame indexing doesn't matter). Everything else the VLM segmented
(far-arm recovery, entry/glide/breath) is kept untouched. On a REFUSE it DROPS
the near-arm recoveries, so the count (collate) AND the per-stroke drilldown
(frontend cycles) both vanish together — one consistent confidence gate.

Runs after phase_segment (so it has the VLM instances to splice into) and before
the per-instance coaches (so they coach the pose recoveries). After the gate, so
a REFUSE short-circuits before any CPU here; only when ``STROKELAB_COACH_POSE_RECOVERY``
is on. Heavy deps (cv2/torch/ultralytics) stay LAZY — this module imports without
them, and the work runs in an ISOLATED subprocess (an OOM/timeout kills only that
child, not the worker). ``count_fn`` is injectable for no-API tests.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable, Optional

from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.types import (
    ComponentResult,
    Granularity,
    InputProfile,
    Instance,
    Phase,
    RunContext,
)

# ctx -> RecoveryResult | None  (None when there's no clip/frames to count)
CountFn = Callable[[RunContext], Awaitable[object]]

# Half-window around a recovery peak (the over-water arc is ~0.8s). Used to window
# strip frames for the per-instance coaches + the frontend cycle thumbnails.
_WIN_HALF_S = 0.4


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


def _rss_kb(pid: int) -> int:
    """Resident memory (KB) of a process from /proc — Linux only; 0 elsewhere (dev/
    macOS), where the watchdog falls back to the timeout."""
    try:
        with open(f"/proc/{pid}/statm") as fh:
            resident_pages = int(fh.read().split()[1])
        import os

        return resident_pages * (os.sysconf("SC_PAGE_SIZE") // 1024)
    except (OSError, ValueError, IndexError):
        return 0


async def _default_count(ctx: RunContext):
    """Run the pose counter in an ISOLATED subprocess so an OOM/timeout kills only
    the child, never the worker. Watches the child's RSS and SIGKILLs it past the
    memory budget or the timeout. Returns a RecoveryResult, or None when pose
    couldn't run (no clip / killed / crashed) — the caller falls back to the VLM
    count, so the analysis always completes."""
    import sys

    path = ctx.video_path
    if not path:
        return None

    from libs.common.config import get_settings

    from services.ai_service.coach.pose import RecoveryResult

    s = get_settings()
    mem_limit_kb = s.STROKELAB_POSE_MEM_LIMIT_MB * 1024
    timeout_s = s.STROKELAB_POSE_TIMEOUT_S

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "services.ai_service.coach.pose_runner",
        str(path),
        str(s.STROKELAB_POSE_MAX_FRAMES),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    # communicate() reads stdout + awaits exit; watch RSS/time concurrently and
    # SIGKILL the child the moment it crosses a budget (keeps the worker alive).
    waiter = asyncio.ensure_future(proc.communicate())
    start = time.monotonic()
    killed = False
    while not waiter.done():
        over_mem = mem_limit_kb > 0 and _rss_kb(proc.pid) > mem_limit_kb
        over_time = timeout_s > 0 and (time.monotonic() - start) > timeout_s
        if over_mem or over_time:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            killed = True
            break
        await asyncio.sleep(0.3)

    try:
        out, _ = await asyncio.wait_for(waiter, timeout=10)
    except (asyncio.TimeoutError, Exception):
        out = b""

    if killed or proc.returncode != 0 or not out:
        return None  # OOM / timeout / crash → pose unavailable; VLM count stands
    try:
        data = json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if not data.get("ok"):
        return None
    return RecoveryResult(
        count=data["count"],
        confidence=data["confidence"],
        detection_rate=data["detection_rate"],
        near_wrist_conf=data["near_wrist_conf"],
        peaks_s=tuple(data.get("peaks_s") or []),
    )


def _payload(result) -> dict:
    """JSON-safe view of a RecoveryResult — stored on ctx + in the cache (peaks_s
    included so a $0 cache replay can rebuild the same recovery instances)."""
    return {
        "count": result.count,
        "confidence": result.confidence,
        "detection_rate": round(result.detection_rate, 3),
        "near_wrist_conf": round(result.near_wrist_conf, 3),
        "refused": result.refused,
        "peaks_s": list(result.peaks_s),
    }


def _recovery_instances(peaks_s) -> list[Instance]:
    """One near-arm recovery Instance per pose peak, windowed by absolute time."""
    out = []
    for i, t in enumerate(sorted(float(p) for p in peaks_s)):
        out.append(
            Instance(
                phase=Phase.RECOVERY,
                instance_id=i,
                start_s=max(0.0, t - _WIN_HALF_S),
                end_s=t + _WIN_HALF_S,
                peak_s=t,
                peak_index=0,  # unused: aspect coaches window the strip by timestamp
                confidence=0.8,
                arm="near",
            )
        )
    return out


def _splice_near_recoveries(ctx: RunContext, pose_instances: list[Instance]) -> None:
    """Replace the near-arm recovery rows of ctx.instances with the pose ones,
    keeping everything else the VLM segmented (far-arm recovery, other phases).
    An empty list (a REFUSE) just drops the near-arm recoveries."""
    kept = [
        i
        for i in (ctx.instances or [])
        if not (i.phase == Phase.RECOVERY and i.arm == "near")
    ]
    ctx.instances = kept + pose_instances


def _apply(ctx: RunContext, payload: dict) -> None:
    """Stash the pose result, splice its recovery instances (drop on refuse), and
    keep ``cache["instances"]`` in sync — that cache projection is what the API
    surfaces as ``result.instances`` (the per-stroke drilldown source), so the
    splice must reach it too, not just the in-memory ctx.instances."""
    from services.ai_service.pipeline.components.segment import _instance_dict

    ctx.pose_recovery = payload
    if payload.get("refused"):
        _splice_near_recoveries(ctx, [])
    else:
        _splice_near_recoveries(ctx, _recovery_instances(payload.get("peaks_s") or []))
    if ctx.cache is not None:
        ctx.cache["instances"] = [_instance_dict(i) for i in (ctx.instances or [])]


class PoseRecoveryComponent(Component):
    name = "pose_recovery"
    consumes = Phase.CLIP
    granularity = Granularity.CHUNK
    profiles = (InputProfile.SIDE_ON_ABOVE, InputProfile.UNKNOWN)

    def __init__(self, count_fn: Optional[CountFn] = None):
        self._count_fn = count_fn

    async def run(self, ctx: RunContext) -> ComponentResult:
        start = time.monotonic()

        # $0 replay — the segmentation is deterministic but costs CPU, so reuse the
        # cached peaks and re-splice the same recovery instances.
        cache = ctx.cache
        if cache is not None and "pose_recovery" in cache:
            payload = cache["pose_recovery"]
            _apply(ctx, payload)
            return ComponentResult(
                self.name,
                [],
                latency_ms=int((time.monotonic() - start) * 1000),
                meta={"replayed": True, **payload},
            )

        count_fn = self._count_fn or _default_count
        result = await count_fn(ctx)
        if result is None:
            ctx.pose_recovery = None  # no clip → leave the VLM instances as-is
            return ComponentResult(
                self.name,
                [],
                latency_ms=int((time.monotonic() - start) * 1000),
                meta={"available": False},
            )

        payload = _payload(result)
        _apply(ctx, payload)  # stash + splice the near-arm recovery instances
        if cache is not None:
            cache["pose_recovery"] = payload

        # No user-facing finding — collate counts the (now pose-derived) instances.
        return ComponentResult(
            self.name,
            [],
            latency_ms=int((time.monotonic() - start) * 1000),
            meta=payload,
        )
