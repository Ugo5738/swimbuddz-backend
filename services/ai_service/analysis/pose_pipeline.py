"""Internal pose-detection + metric pipeline.

This module owns:
  * MediaPipe pose-landmarker model download + cache
  * YOLOv8n swimmer-box detection + heuristic selection
  * Crop → pose-on-crop → landmark mapping back to original frame
  * Stroke rate, body roll, breath balance computation
  * Annotated-video rendering

It does NOT own:
  * Storage I/O (callers pass local file paths)
  * LLM summary (see summary.py)
  * Database (see services.ai_service.tasks.analyze)

Validated end-to-end against real cohort footage during the Week 1 kill
gate (~86% pose detection on a 4K iPhone clip with multiple deck people
in the frame). See docs/design/AI_SWIM_ANALYZER_DESIGN.md for the design
context and the kill-gate criteria.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from libs.common.logging import get_logger

logger = get_logger(__name__)


def _transcode_to_h264(path: Path) -> None:
    """Re-encode an mp4v file to browser-playable H.264 in place.

    Writes to a sibling temp file then atomically replaces the original.
    No-op (with a warning) if ffmpeg is unavailable or the encode fails —
    the caller treats the annotated video as best-effort.
    """
    if shutil.which("ffmpeg") is None:
        logger.warning("ffmpeg not found; leaving annotated video as mp4v")
        return
    tmp = path.with_name(path.stem + ".h264.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",  # required for broad browser support
        "-movflags",
        "+faststart",  # moov atom up front for web streaming/seeking
        "-an",
        str(tmp),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("ffmpeg transcode raised %s; keeping mp4v", exc)
        return
    if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
        tmp.replace(path)
    else:
        logger.warning(
            "ffmpeg transcode failed (rc=%s); keeping mp4v. stderr: %s",
            result.returncode,
            (result.stderr or "")[-300:],
        )
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ── MediaPipe pose landmark indices ───────────────────────────────
NOSE = 0
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_WRIST = 15
RIGHT_WRIST = 16
LEFT_HIP = 23
RIGHT_HIP = 24
LEFT_KNEE = 25
RIGHT_KNEE = 26
LEFT_ANKLE = 27
RIGHT_ANKLE = 28

POSE_CONNECTIONS: list[tuple[int, int]] = [
    (LEFT_SHOULDER, RIGHT_SHOULDER),
    (LEFT_SHOULDER, LEFT_HIP),
    (RIGHT_SHOULDER, RIGHT_HIP),
    (LEFT_HIP, RIGHT_HIP),
    (LEFT_SHOULDER, 13),
    (13, LEFT_WRIST),
    (RIGHT_SHOULDER, 14),
    (14, RIGHT_WRIST),
    (LEFT_HIP, 25),
    (25, 27),
    (RIGHT_HIP, 26),
    (26, 28),
    (NOSE, LEFT_SHOULDER),
    (NOSE, RIGHT_SHOULDER),
]

POSE_MODEL_URLS = {
    "lite": (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
    ),
    "full": (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_full/float16/latest/pose_landmarker_full.task"
    ),
    "heavy": (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task"
    ),
}

# Default to /tmp; the ARQ worker container can override via STROKELAB_MODEL_DIR.
# Persist the model across worker restarts by mounting a volume here.
import os as _os  # noqa: E402

POSE_MODEL_CACHE_DIR = Path(
    _os.environ.get("STROKELAB_MODEL_DIR", "/tmp/strokelab-models")
)


# ── Small dataclasses ─────────────────────────────────────────────


@dataclass
class _MappedLandmark:
    """A pose landmark in the original frame's [0,1] coordinate space."""

    x: float
    y: float
    visibility: float


@dataclass
class _FrameSample:
    """The landmarks we consume downstream, per frame. Knees + ankles are
    here for the kick assessment — they're frequently None (legs underwater)
    so every consumer must handle absence."""

    t: float
    nose: Optional[tuple[float, float]]
    l_shoulder: Optional[tuple[float, float]]
    r_shoulder: Optional[tuple[float, float]]
    l_wrist: Optional[tuple[float, float]]
    r_wrist: Optional[tuple[float, float]]
    l_hip: Optional[tuple[float, float]]
    r_hip: Optional[tuple[float, float]]
    l_knee: Optional[tuple[float, float]] = None
    r_knee: Optional[tuple[float, float]] = None
    l_ankle: Optional[tuple[float, float]] = None
    r_ankle: Optional[tuple[float, float]] = None


# ── Model asset loaders ───────────────────────────────────────────


def ensure_pose_model(variant: str) -> Path:
    """Download a MediaPipe pose-landmarker .task model on first use."""
    if variant not in POSE_MODEL_URLS:
        raise ValueError(f"Unknown pose model variant: {variant}")
    cache_path = POSE_MODEL_CACHE_DIR / f"pose_landmarker_{variant}.task"
    if cache_path.exists():
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(POSE_MODEL_URLS[variant], cache_path)
    return cache_path


def load_yolo_model():
    """Load YOLOv8n. ultralytics auto-downloads weights into ~/.config/Ultralytics."""
    from ultralytics import YOLO  # heavy import — lazy

    return YOLO("yolov8n.pt")


# ── Helpers ───────────────────────────────────────────────────────


def _xy(landmark, min_vis: float = 0.3) -> Optional[tuple[float, float]]:
    if landmark is None:
        return None
    vis = getattr(landmark, "visibility", None)
    if vis is not None and vis < min_vis:
        return None
    return (landmark.x, landmark.y)


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


def _signed_angle_deg(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dy, dx))


def _draw_pose(frame: np.ndarray, landmarks: list[_MappedLandmark]) -> None:
    """Draw skeleton + keypoints onto frame in place (BGR image)."""
    h, w = frame.shape[:2]
    pts: dict[int, tuple[int, int]] = {}
    for idx, lm in enumerate(landmarks):
        if lm.visibility < 0.3:
            continue
        x = int(lm.x * w)
        y = int(lm.y * h)
        pts[idx] = (x, y)
        cv2.circle(frame, (x, y), 4, (0, 255, 0), -1)
    for a, b in POSE_CONNECTIONS:
        if a in pts and b in pts:
            cv2.line(frame, pts[a], pts[b], (0, 200, 255), 2)


def _draw_box(frame: np.ndarray, box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 100, 0), 2)


# ── YOLO swimmer detection ────────────────────────────────────────


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


def _map_landmarks(
    crop_landmarks,
    crop_box: tuple[int, int, int, int],
    orig_w: int,
    orig_h: int,
) -> list[_MappedLandmark]:
    """Translate landmarks from crop-relative [0,1] to original-frame [0,1]."""
    cx1, cy1, cx2, cy2 = crop_box
    cw = cx2 - cx1
    ch = cy2 - cy1
    return [
        _MappedLandmark(
            x=(cx1 + lm.x * cw) / orig_w,
            y=(cy1 + lm.y * ch) / orig_h,
            visibility=float(getattr(lm, "visibility", 1.0)),
        )
        for lm in crop_landmarks
    ]


# ── Metric computations ───────────────────────────────────────────


def _compute_metrics(samples: list[_FrameSample], fps: float) -> dict:
    if not samples:
        return {}
    duration_s = samples[-1].t - samples[0].t
    if duration_s <= 0:
        return {}

    # ── Stroke rate: count wrist-recovery peaks (each arm cycle = 1 stroke)
    def _wrist_track(side: str) -> np.ndarray:
        vals: list[float] = []
        for s in samples:
            wrist = s.l_wrist if side == "l" else s.r_wrist
            if s.l_shoulder and s.r_shoulder:
                shoulder_mid = (
                    (s.l_shoulder[0] + s.r_shoulder[0]) / 2,
                    (s.l_shoulder[1] + s.r_shoulder[1]) / 2,
                )
            else:
                shoulder_mid = None
            if s.l_hip and s.r_hip:
                hip_mid = (
                    (s.l_hip[0] + s.r_hip[0]) / 2,
                    (s.l_hip[1] + s.r_hip[1]) / 2,
                )
            else:
                hip_mid = None
            if wrist is None or shoulder_mid is None or hip_mid is None:
                vals.append(np.nan)
                continue
            torso_len = abs(hip_mid[1] - shoulder_mid[1]) or 1e-6
            vals.append((shoulder_mid[1] - wrist[1]) / torso_len)
        return np.array(vals, dtype=float)

    l_track = _wrist_track("l")
    r_track = _wrist_track("r")
    min_distance = max(1, int(fps * 0.33))  # cap at 180 SPM per arm
    l_peaks = _local_peaks(l_track, min_distance)
    r_peaks = _local_peaks(r_track, min_distance)
    total_strokes = len(l_peaks) + len(r_peaks)
    stroke_rate_spm = (total_strokes / duration_s) * 60.0 if duration_s > 0 else 0.0

    # ── Body roll proxy: shoulder-line tilt, folded to [0, 90]
    roll_values: list[float] = []
    for s in samples:
        if s.l_shoulder is None or s.r_shoulder is None:
            continue
        dx = s.r_shoulder[0] - s.l_shoulder[0]
        dy = -(s.r_shoulder[1] - s.l_shoulder[1])
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            continue
        raw = abs(_signed_angle_deg(dx, dy)) % 180
        folded = raw if raw <= 90 else (180 - raw)
        roll_values.append(folded)
    body_roll_deg = float(np.mean(roll_values)) if roll_values else None

    # ── Breath balance: count sustained head-turn events left vs right
    states: list[str] = []
    for s in samples:
        if s.nose is None or s.l_shoulder is None or s.r_shoulder is None:
            states.append("none")
            continue
        shoulder_w = abs(s.r_shoulder[0] - s.l_shoulder[0]) or 1e-6
        mid_x = (s.l_shoulder[0] + s.r_shoulder[0]) / 2
        offset = (s.nose[0] - mid_x) / shoulder_w
        if offset > 0.18:
            states.append("right")
        elif offset < -0.18:
            states.append("left")
        else:
            states.append("neutral")

    min_breath_frames = max(1, int(fps * 0.25))
    breath_left = 0
    breath_right = 0
    i = 0
    while i < len(states):
        st = states[i]
        if st in ("left", "right"):
            j = i
            while j < len(states) and states[j] == st:
                j += 1
            if j - i >= min_breath_frames:
                if st == "left":
                    breath_left += 1
                else:
                    breath_right += 1
            i = j
        else:
            i += 1
    total_breaths = breath_left + breath_right
    breath_balance = (
        round(breath_left / total_breaths, 3) if total_breaths > 0 else None
    )

    return {
        "stroke_rate_spm": round(stroke_rate_spm, 1),
        "stroke_peaks_left_arm": len(l_peaks),
        "stroke_peaks_right_arm": len(r_peaks),
        "body_roll_proxy_degrees": (
            round(body_roll_deg, 1) if body_roll_deg is not None else None
        ),
        "breath_count_left": breath_left,
        "breath_count_right": breath_right,
        "breath_balance_left_ratio": breath_balance,
    }


# ── Observations + tracking gaps ──────────────────────────────────
#
# Deterministic, threshold-based technique flags derived from the pose
# timeseries. Each observation carries a representative timestamp (so the
# UI can seek the annotated video to that moment) and an optional drill_key
# into services.ai_service.analysis.drills.
#
# Design rules:
#   * Never fabricate. If we can't see something (legs underwater), say so
#     rather than guess — preserves the kill-gate "zero false positives".
#   * Severity is one of: "good" | "suggestion" | "unavailable".
#   * Body roll + breathing are solid signals (we see shoulders + head).
#     Kick is best-effort (legs are usually submerged). Stroke rate is a
#     proxy that tends to over-count, so its copy is hedged.

# Tracking gaps shorter than this are noise (a splash for a few frames);
# longer ones are worth surfacing ("we lost you here").
_MIN_GAP_SECONDS = 0.4

# Below this average shoulder-line tilt we call rotation "flat".
_LOW_ROTATION_DEG = 22.0
# Breath ratio outside [0.35, 0.65] counts as one-sided.
_BREATH_SKEW_LOW = 0.35
_BREATH_SKEW_HIGH = 0.65
# A stroke rate above this is flagged as possibly "spinning" (hedged copy).
_HIGH_SPM = 115.0
# We only assess kick if knees+ankles were visible in at least this share
# of pose-detected frames.
_MIN_LEG_VISIBILITY = 0.25
# Average knee-bend below this (i.e. more bent) reads as knee-driven.
_KNEE_DRIVEN_ANGLE_DEG = 140.0


def _frame_roll_deg(s: _FrameSample) -> Optional[float]:
    if s.l_shoulder is None or s.r_shoulder is None:
        return None
    dx = s.r_shoulder[0] - s.l_shoulder[0]
    dy = -(s.r_shoulder[1] - s.l_shoulder[1])
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return None
    raw = abs(_signed_angle_deg(dx, dy)) % 180
    return raw if raw <= 90 else (180 - raw)


def _knee_angle_deg(
    hip: Optional[tuple[float, float]],
    knee: Optional[tuple[float, float]],
    ankle: Optional[tuple[float, float]],
) -> Optional[float]:
    """Interior angle at the knee (180° = straight leg)."""
    if hip is None or knee is None or ankle is None:
        return None
    ax, ay = hip[0] - knee[0], hip[1] - knee[1]
    bx, by = ankle[0] - knee[0], ankle[1] - knee[1]
    na = math.hypot(ax, ay)
    nb = math.hypot(bx, by)
    if na < 1e-6 or nb < 1e-6:
        return None
    cosang = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
    return math.degrees(math.acos(cosang))


def _compute_observations(
    samples: list[_FrameSample], fps: float, metrics: dict
) -> dict:
    observations: list[dict] = []
    tracking_gaps: list[dict] = []
    if not samples:
        return {"observations": observations, "tracking_gaps": tracking_gaps}

    # ── Tracking gaps: runs of no-pose samples lasting >= _MIN_GAP_SECONDS
    i = 0
    n = len(samples)
    while i < n:
        has_pose = samples[i].l_shoulder is not None or samples[i].nose is not None
        if not has_pose:
            j = i
            while j < n and not (
                samples[j].l_shoulder is not None or samples[j].nose is not None
            ):
                j += 1
            start_t = samples[i].t
            end_t = samples[j - 1].t
            if (end_t - start_t) >= _MIN_GAP_SECONDS:
                tracking_gaps.append(
                    {
                        "start_s": round(start_t, 1),
                        "end_s": round(end_t, 1),
                        "duration_s": round(end_t - start_t, 1),
                    }
                )
            i = j
        else:
            i += 1

    # ── Shoulder rotation (from per-frame roll)
    rolls = [(s.t, _frame_roll_deg(s)) for s in samples]
    rolls = [(t, r) for t, r in rolls if r is not None]
    mean_roll = metrics.get("body_roll_proxy_degrees")
    if rolls and mean_roll is not None:
        if mean_roll < _LOW_ROTATION_DEG:
            min_t, _min_r = min(rolls, key=lambda tr: tr[1])
            observations.append(
                {
                    "key": "low_rotation",
                    "severity": "suggestion",
                    "title": "Limited shoulder rotation",
                    "detail": (
                        f"Your shoulders averaged {mean_roll:.0f}° of tilt — fairly "
                        f"flat. Rotating more from the core helps you reach further "
                        f"and breathe with less strain."
                    ),
                    "timestamp_s": round(min_t, 1),
                    "drill_key": "low_rotation",
                }
            )
        else:
            observations.append(
                {
                    "key": "rotation_ok",
                    "severity": "good",
                    "title": "Good shoulder rotation",
                    "detail": f"Nice — your shoulders rotated about {mean_roll:.0f}° on average.",
                    "timestamp_s": None,
                    "drill_key": None,
                }
            )

    # ── Breathing balance
    bl = metrics.get("breath_count_left") or 0
    br = metrics.get("breath_count_right") or 0
    ratio = metrics.get("breath_balance_left_ratio")
    if (bl + br) >= 3 and ratio is not None:
        if ratio < _BREATH_SKEW_LOW or ratio > _BREATH_SKEW_HIGH:
            dominant = "right" if ratio < 0.5 else "left"
            # Representative moment: first sustained breath toward the dominant side.
            ts = _first_breath_timestamp(samples, dominant)
            observations.append(
                {
                    "key": "one_sided_breathing",
                    "severity": "suggestion",
                    "title": "Mostly one-sided breathing",
                    "detail": (
                        f"You breathed mostly to your {dominant} ({bl}L / {br}R). "
                        f"Alternating sides keeps your stroke even and your line "
                        f"straight."
                    ),
                    "timestamp_s": ts,
                    "drill_key": "one_sided_breathing",
                }
            )
        else:
            observations.append(
                {
                    "key": "breathing_ok",
                    "severity": "good",
                    "title": "Balanced breathing",
                    "detail": f"Good balance — {bl} left / {br} right.",
                    "timestamp_s": None,
                    "drill_key": None,
                }
            )

    # ── Kick (best-effort; legs are usually submerged)
    leg_frames = 0
    knee_angles: list[float] = []
    for s in samples:
        la = _knee_angle_deg(s.l_hip, s.l_knee, s.l_ankle)
        ra = _knee_angle_deg(s.r_hip, s.r_knee, s.r_ankle)
        if la is not None or ra is not None:
            leg_frames += 1
            for a in (la, ra):
                if a is not None:
                    knee_angles.append(a)
    pose_frames = sum(1 for s in samples if s.l_shoulder is not None)
    leg_visibility = leg_frames / pose_frames if pose_frames else 0.0

    if leg_visibility < _MIN_LEG_VISIBILITY or not knee_angles:
        observations.append(
            {
                "key": "kick_unavailable",
                "severity": "unavailable",
                "title": "Couldn't assess your kick",
                "detail": (
                    "Your legs were underwater / out of view for most of this "
                    "clip, so we can't measure your kick. Film side-on with your "
                    "legs near the surface for a kick read."
                ),
                "timestamp_s": None,
                "drill_key": None,
            }
        )
    else:
        avg_knee = sum(knee_angles) / len(knee_angles)
        if avg_knee < _KNEE_DRIVEN_ANGLE_DEG:
            observations.append(
                {
                    "key": "knee_driven_kick",
                    "severity": "suggestion",
                    "title": "Kick looks knee-driven",
                    "detail": (
                        f"Your knees bent to about {avg_knee:.0f}° on average, which "
                        f"suggests a bicycle-style kick. Driving from the hips and "
                        f"glutes with longer legs cuts drag."
                    ),
                    "timestamp_s": None,
                    "drill_key": "knee_driven_kick",
                }
            )
        else:
            observations.append(
                {
                    "key": "kick_ok",
                    "severity": "good",
                    "title": "Kick looks hip-driven",
                    "detail": (
                        f"Legs stayed fairly long (avg knee {avg_knee:.0f}°) — that's "
                        f"an efficient flutter kick."
                    ),
                    "timestamp_s": None,
                    "drill_key": None,
                }
            )

    # ── Stroke rate (proxy — hedge the copy)
    spm = metrics.get("stroke_rate_spm")
    if spm is not None and spm > _HIGH_SPM:
        observations.append(
            {
                "key": "high_stroke_rate",
                "severity": "suggestion",
                "title": "High stroke rate",
                "detail": (
                    f"We counted roughly {spm:.0f} strokes/min — on the high side, "
                    f"which can mean short, hurried pulls. (This metric is an "
                    f"estimate.) Lengthening each stroke often adds speed with less "
                    f"effort."
                ),
                "timestamp_s": None,
                "drill_key": "high_stroke_rate",
            }
        )

    return {"observations": observations, "tracking_gaps": tracking_gaps}


def _first_breath_timestamp(samples: list[_FrameSample], side: str) -> Optional[float]:
    """Timestamp of the first frame the nose is clearly turned to ``side``."""
    for s in samples:
        if s.nose is None or s.l_shoulder is None or s.r_shoulder is None:
            continue
        shoulder_w = abs(s.r_shoulder[0] - s.l_shoulder[0]) or 1e-6
        mid_x = (s.l_shoulder[0] + s.r_shoulder[0]) / 2
        offset = (s.nose[0] - mid_x) / shoulder_w
        if side == "right" and offset > 0.18:
            return round(s.t, 1)
        if side == "left" and offset < -0.18:
            return round(s.t, 1)
    return None


# ── The main pipeline runner ──────────────────────────────────────


def analyse_video(
    video_path: Path,
    annotated_out_path: Path,
    *,
    pose_model_variant: str,
    max_inference_side: int,
    use_yolo: bool,
    yolo_conf_threshold: float,
    frame_stride: int,
    box_pad_ratio: float = 0.18,
) -> dict:
    """Run the full pose pipeline on one video file. Returns a dict report.

    This is the workhorse the ARQ task wraps. It's deliberately synchronous
    and CPU-bound — caller is responsible for running it in a thread/executor
    if invoking from an async context.
    """
    # mediapipe is imported lazily so the AI *API* service (and CI's openapi
    # generation, which installs only .[dev]) can import this module without the
    # heavy ML stack. Only the worker, which actually runs analysis, needs it.
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    if not video_path.exists():
        raise FileNotFoundError(video_path)
    annotated_out_path.parent.mkdir(parents=True, exist_ok=True)

    pose_model_path = ensure_pose_model(pose_model_variant)
    yolo_model = load_yolo_model() if use_yolo else None

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cv2 could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_s = n_frames_total / fps if fps > 0 else 0.0

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    # Write at the input fps so the annotated mp4 has the same wall-clock
    # duration as the upload — even when frame_stride > 1 and we duplicate
    # frames between detections.
    writer = cv2.VideoWriter(str(annotated_out_path), fourcc, fps, (width, height))

    options = mp_vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(pose_model_path)),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.4,
        min_pose_presence_confidence=0.4,
        min_tracking_confidence=0.4,
        output_segmentation_masks=False,
    )

    samples: list[_FrameSample] = []
    frames_with_pose = 0
    frames_processed = 0
    frames_with_yolo_box = 0
    last_box: Optional[tuple[int, int, int, int]] = None
    last_mapped: Optional[list[_MappedLandmark]] = None

    t_start = time.time()
    with mp_vision.PoseLandmarker.create_from_options(options) as landmarker:
        frame_idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            # Frame skipping for latency: only run pose on every N-th frame
            # and write a duplicate annotated frame in between. Tracking
            # smooths over the gap since YOLO/pose state carries forward.
            run_pose_this_frame = frame_idx % frame_stride == 0
            t = frame_idx / fps if fps > 0 else 0.0
            ts_ms = int(t * 1000)

            longer = max(width, height)
            if longer > max_inference_side:
                scale = max_inference_side / longer
                infer_w = int(round(width * scale))
                infer_h = int(round(height * scale))
                infer_frame = cv2.resize(
                    frame, (infer_w, infer_h), interpolation=cv2.INTER_AREA
                )
            else:
                infer_frame = frame
                scale = 1.0

            box_orig: Optional[tuple[int, int, int, int]] = None
            mapped: Optional[list[_MappedLandmark]] = None

            if run_pose_this_frame:
                frames_processed += 1
                # 1. YOLO swimmer-box (or carry forward)
                if yolo_model is not None:
                    yb = detect_swimmer_box(
                        yolo_model, infer_frame, min_conf=yolo_conf_threshold
                    )
                    if yb is not None:
                        inv = 1.0 / scale
                        box_orig = (
                            int(yb[0] * inv),
                            int(yb[1] * inv),
                            int(yb[2] * inv),
                            int(yb[3] * inv),
                        )
                        last_box = box_orig
                    else:
                        box_orig = last_box
                    if box_orig is not None:
                        frames_with_yolo_box += 1

                # 2. Pose inference on crop (or full frame if no YOLO)
                if box_orig is not None:
                    crop_box = _expand_box(box_orig, width, height, box_pad_ratio)
                    cx1, cy1, cx2, cy2 = crop_box
                    crop = frame[cy1:cy2, cx1:cx2]
                    if crop.size > 0:
                        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                        rgb = np.ascontiguousarray(rgb)
                        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                        result = landmarker.detect_for_video(mp_image, ts_ms)
                        if result.pose_landmarks:
                            mapped = _map_landmarks(
                                result.pose_landmarks[0], crop_box, width, height
                            )
                elif yolo_model is None:
                    # No YOLO mode: run pose on whole downscaled frame
                    rgb = cv2.cvtColor(infer_frame, cv2.COLOR_BGR2RGB)
                    rgb = np.ascontiguousarray(rgb)
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                    result = landmarker.detect_for_video(mp_image, ts_ms)
                    if result.pose_landmarks:
                        mapped = _map_landmarks(
                            result.pose_landmarks[0],
                            (0, 0, width, height),
                            width,
                            height,
                        )

                last_mapped = mapped
            else:
                # Skipped frame: replay last-known box + landmarks for the
                # annotated output, but don't count it toward stats.
                box_orig = last_box
                mapped = last_mapped

            # Build sample row + record pose detection (only on processed frames)
            if run_pose_this_frame and mapped is not None:
                frames_with_pose += 1
                samples.append(
                    _FrameSample(
                        t=t,
                        nose=_xy(mapped[NOSE]),
                        l_shoulder=_xy(mapped[LEFT_SHOULDER]),
                        r_shoulder=_xy(mapped[RIGHT_SHOULDER]),
                        l_wrist=_xy(mapped[LEFT_WRIST]),
                        r_wrist=_xy(mapped[RIGHT_WRIST]),
                        l_hip=_xy(mapped[LEFT_HIP]),
                        r_hip=_xy(mapped[RIGHT_HIP]),
                        l_knee=_xy(mapped[LEFT_KNEE]),
                        r_knee=_xy(mapped[RIGHT_KNEE]),
                        l_ankle=_xy(mapped[LEFT_ANKLE]),
                        r_ankle=_xy(mapped[RIGHT_ANKLE]),
                    )
                )
            elif run_pose_this_frame:
                samples.append(
                    _FrameSample(
                        t=t,
                        nose=None,
                        l_shoulder=None,
                        r_shoulder=None,
                        l_wrist=None,
                        r_wrist=None,
                        l_hip=None,
                        r_hip=None,
                    )
                )

            # Render annotated frame (always — keeps output duration intact)
            annotated = frame.copy()
            if box_orig is not None:
                _draw_box(
                    annotated, _expand_box(box_orig, width, height, box_pad_ratio)
                )
            if mapped is not None:
                _draw_pose(annotated, mapped)
            writer.write(annotated)

            frame_idx += 1

    cap.release()
    writer.release()

    # OpenCV's headless build can only write mp4v (MPEG-4 Part 2), which
    # browsers refuse to play in an HTML5 <video> (you get a black player
    # with working controls). Transcode to H.264 + yuv420p so it plays
    # everywhere. Best-effort: keep the mp4v file if ffmpeg is missing or
    # errors — a non-playable annotated video shouldn't fail the analysis.
    _transcode_to_h264(annotated_out_path)

    processing_seconds = time.time() - t_start

    effective_fps = fps / max(1, frame_stride)
    metrics = _compute_metrics(samples, effective_fps)
    insights = _compute_observations(samples, effective_fps, metrics)
    pose_detection_rate = (
        frames_with_pose / frames_processed if frames_processed else 0.0
    )
    yolo_detection_rate = (
        frames_with_yolo_box / frames_processed if frames_processed else 0.0
    )

    return {
        "source_resolution": f"{width}x{height}",
        "duration_seconds": round(duration_s, 2),
        "fps": round(fps, 2),
        "frames_total": frame_idx,
        "frames_processed": frames_processed,
        "frames_with_pose": frames_with_pose,
        "pose_detection_rate": round(pose_detection_rate, 3),
        "yolo_detection_rate": round(yolo_detection_rate, 3),
        "processing_seconds": round(processing_seconds, 2),
        "realtime_ratio": (
            round(processing_seconds / duration_s, 2) if duration_s > 0 else None
        ),
        **metrics,
        **insights,
    }
