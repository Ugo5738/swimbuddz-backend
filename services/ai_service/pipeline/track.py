"""Stage 0 — Ingest & Track.

Decode the WHOLE clip (strided) and track the swimmer as a BOX across every
sampled frame, plus an upper-box "over-water arm" motion signal that Stage 1
peak-detects into recovery instances. Box-only — never trusts in-water pose.

Reuses the proven detectors from ``coach.select`` (YOLO, or a cv2 motion fallback
when torch is absent). Needs cv2 (runs in the ai-worker container / local python3
with OpenCV) — it is NOT import-light.
"""

from __future__ import annotations

import math
from pathlib import Path

from services.ai_service.pipeline.types import Track, TrackPoint

_GRID = 64  # motion signal is computed on a fixed small grid so it's box-size invariant


def _smooth_boxes(boxes: list, window: int = 5) -> list:
    """Median-filter box coords to kill frame-to-frame jitter (which otherwise
    fakes motion-signal peaks as the normalized crop shifts under it)."""
    import numpy as np

    idx = [i for i, b in enumerate(boxes) if b]
    if len(idx) < window:
        return boxes
    arr = np.array([boxes[i] for i in idx], dtype=float)
    half = window // 2
    out = arr.copy()
    for j in range(len(arr)):
        out[j] = np.median(arr[max(0, j - half) : j + half + 1], axis=0)
    smoothed = list(boxes)
    for k, i in enumerate(idx):
        smoothed[i] = tuple(int(v) for v in out[k])
    return smoothed


def build_track(
    clip_path: str | Path,
    *,
    detector: str = "auto",  # "auto" | "yolo" | "motion"
    stride: int = 2,
    work_long_edge: int = 720,
    max_frames: int = 300,
) -> Track:
    import cv2
    import numpy as np

    from services.ai_service.coach.frames import _resize_long_edge
    from services.ai_service.coach.select import (
        _detect_boxes_motion,
        _detect_boxes_yolo,
        _fill_boxes,
    )

    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {clip_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total > 0:  # widen stride so we never hold more than ~max_frames
        stride = max(stride, math.ceil(total / max_frames))

    frames, times, idx = [], [], 0
    while True:
        if not cap.grab():
            break
        if idx % stride == 0:
            ok, img = cap.retrieve()
            if ok and img is not None:
                frames.append(_resize_long_edge(img, work_long_edge))
                times.append(idx / fps)
        idx += 1
    cap.release()
    if not frames:
        raise RuntimeError(f"no frames decoded from {clip_path}")
    fh, fw = frames[0].shape[:2]

    fallback = False
    det = detector
    if det in ("auto", "yolo"):
        try:
            boxes = _detect_boxes_yolo(frames)
            det = "yolo"
        except Exception:
            if detector == "yolo":
                raise
            boxes = _detect_boxes_motion(frames)
            det, fallback = "motion", True
    else:
        boxes = _detect_boxes_motion(frames)
        det = "motion"
    boxes = _smooth_boxes(_fill_boxes(boxes))

    frame_area = float(fw * fh)
    points: list[TrackPoint] = []
    prev = None
    for i, (img, t, b) in enumerate(zip(frames, times, boxes)):
        area_frac = ((b[2] - b[0]) * (b[3] - b[1]) / frame_area) if b else 0.0
        motion = 0.0
        if b:
            x1, y1, x2, y2 = b
            crop = img[y1:y2, x1:x2]
            if crop.size:
                gray = cv2.resize(
                    cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (_GRID, _GRID)
                )
                upper = gray[: _GRID // 2, :].astype(
                    "float32"
                )  # over-water arm lives up top
                if prev is not None:
                    motion = float(np.abs(upper - prev).mean())
                prev = upper
        points.append(
            TrackPoint(
                index=i, timestamp_s=t, box=b, area_frac=area_frac, motion=motion
            )
        )

    return Track(
        points=points,
        fps=fps,
        frame_w=fw,
        frame_h=fh,
        detector=det,
        fallback_used=fallback,
    )
