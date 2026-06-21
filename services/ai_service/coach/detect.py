"""Swimmer detection + peak-finding utilities for the VLM coach pipeline
(Stage-1 recovery segmentation and key-frame selection).

Extracted from the legacy ``analysis/pose_pipeline.py`` so the coach no longer
imports the old metrics engine. Heavy deps (ultralytics / YOLO) stay LAZY so the
AI app still imports without cv2 / mediapipe / torch — the CI + openapi-gen
condition. Pure numpy/math at module load.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np


def load_yolo_model():
    """Load YOLOv8n. ultralytics auto-downloads weights into ~/.config/Ultralytics."""
    from ultralytics import YOLO  # heavy import — lazy

    return YOLO("yolov8n.pt")


def _local_peaks(values: np.ndarray, min_distance: int) -> list[int]:
    """Find strict local maxima at least ``min_distance`` apart. NaN-safe."""
    n = len(values)
    peaks: list[int] = []
    i = 1
    while i < n - 1:
        v = values[i]
        if math.isnan(v):
            i += 1
            continue
        lo = max(0, i - min_distance)
        hi = min(n, i + min_distance + 1)
        window = values[lo:hi]
        finite_window = window[~np.isnan(window)]
        if len(finite_window) == 0:
            i += 1
            continue
        if v >= np.max(finite_window) and v > values[i - 1]:
            peaks.append(i)
            i += min_distance
        else:
            i += 1
    return peaks


def detect_swimmer_box(
    yolo_model,
    frame: np.ndarray,
    min_conf: float,
) -> Optional[tuple[int, int, int, int]]:
    """Run YOLO person detection and pick the box most likely to be the swimmer.

    Heuristic — swimmers are prone (wide+short box) and below the deck:
      * favour horizontal aspect ratio
      * penalise boxes whose centre is in the upper third of the frame
        (those are usually deck spectators standing at the wall)
      * gentle area bonus to break ties in favour of the closer subject

    Returns the bbox in frame pixel coords, or None if no person detected.
    """
    res = yolo_model(frame, classes=[0], conf=min_conf, verbose=False, imgsz=640)
    fh = frame.shape[0]
    best: Optional[tuple[int, int, int, int]] = None
    best_score = -math.inf
    for r in res:
        boxes = r.boxes
        if boxes is None:
            continue
        xyxy = boxes.xyxy.cpu().numpy()
        for x1, y1, x2, y2 in xyxy:
            w = float(x2 - x1)
            h = float(y2 - y1)
            if w <= 0 or h <= 0:
                continue
            aspect = w / h
            area = w * h
            cy = (y1 + y2) / 2 / fh  # 0=top, 1=bottom of frame
            upper_penalty = max(0.0, 0.35 - cy) * 5
            score = aspect + 0.0002 * area - upper_penalty
            if score > best_score:
                best_score = score
                best = (int(x1), int(y1), int(x2), int(y2))
    return best


def _expand_box(
    box: tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
    pad_ratio: float,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    w = x2 - x1
    h = y2 - y1
    px = int(round(w * pad_ratio))
    py = int(round(h * pad_ratio))
    return (
        max(0, x1 - px),
        max(0, y1 - py),
        min(frame_w, x2 + px),
        min(frame_h, y2 + py),
    )
