"""
Stroke Lab — local kill-gate proof of concept.

Standalone CLI: takes a freestyle swim video, detects the swimmer with
YOLOv8n, crops to the swimmer's bounding box, runs MediaPipe Pose Landmarker
on the crop, computes stroke rate / body roll proxy / breath balance, writes
an annotated video, and prints a JSON report.

Run from the strokelab venv:

    /tmp/strokelab-venv/bin/python services/ai_service/scripts/analyze_local.py \
        --video path/to/swim.mp4 \
        --out-dir /tmp/strokelab-out

The output JSON includes the three kill-gate metrics:
  * pose_detection_rate  — fraction of frames where pose landmarks were detected
  * stroke_rate_spm      — strokes per minute (compare against manual count)
  * processing_seconds   — wall-clock processing time

This script is intentionally free of SwimBuddz module imports — the kill gate
should be testable before any DB / FastAPI plumbing exists.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

# MediaPipe pose landmark indices (same as legacy)
NOSE = 0
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_WRIST = 15
RIGHT_WRIST = 16
LEFT_HIP = 23
RIGHT_HIP = 24

POSE_CONNECTIONS = [
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

MODEL_URLS = {
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
MODEL_CACHE_DIR = Path("/tmp/strokelab-models")


@dataclass
class MappedLandmark:
    """A pose landmark in the original frame's [0,1] coordinate space."""

    x: float
    y: float
    visibility: float


@dataclass
class FrameSample:
    t: float
    nose: tuple[float, float] | None
    l_shoulder: tuple[float, float] | None
    r_shoulder: tuple[float, float] | None
    l_wrist: tuple[float, float] | None
    r_wrist: tuple[float, float] | None
    l_hip: tuple[float, float] | None
    r_hip: tuple[float, float] | None


def ensure_model(variant: str) -> Path:
    if variant not in MODEL_URLS:
        raise ValueError(f"Unknown model variant: {variant}")
    cache_path = MODEL_CACHE_DIR / f"pose_landmarker_{variant}.task"
    if cache_path.exists():
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"Downloading pose model ({variant}) from {MODEL_URLS[variant]}",
        file=sys.stderr,
    )
    urllib.request.urlretrieve(MODEL_URLS[variant], cache_path)
    return cache_path


def _xy(landmark, min_vis: float = 0.3) -> tuple[float, float] | None:
    if landmark is None:
        return None
    vis = getattr(landmark, "visibility", None)
    if vis is not None and vis < min_vis:
        return None
    return (landmark.x, landmark.y)


def _local_peaks(values: np.ndarray, min_distance: int) -> list[int]:
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


def _draw_pose(frame: np.ndarray, landmarks) -> None:
    """Draw skeleton + keypoints onto frame in place (BGR image)."""
    h, w = frame.shape[:2]
    pts: dict[int, tuple[int, int]] = {}
    for idx, lm in enumerate(landmarks):
        vis = getattr(lm, "visibility", 1.0)
        if vis is None or vis < 0.3:
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


def detect_swimmer_box(
    yolo_model,
    frame: np.ndarray,
    min_conf: float = 0.25,
) -> tuple[int, int, int, int] | None:
    """
    Run YOLO person detection on the frame, return the swimmer's bounding box
    in the frame's pixel coordinates, or None.

    Heuristic for picking the swimmer among detected persons:
      * Prefer boxes with horizontal aspect ratio (width > height) — swimmers
        are prone; deck people are upright.
      * Among ties, prefer larger area (the prominent subject).
      * Cap deck-people bias by penalising boxes whose centre is in the
        upper 30% of the frame (deck is usually above the water).
    """
    res = yolo_model(frame, classes=[0], conf=min_conf, verbose=False, imgsz=640)
    fh, fw = frame.shape[:2]
    best = None
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
            aspect = w / h  # >1 = horizontal subject
            area = w * h
            cy = (y1 + y2) / 2 / fh  # 0=top, 1=bottom
            # Penalty for upper-third boxes (likely deck spectators)
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
    pad_ratio: float = 0.18,
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
) -> list[MappedLandmark]:
    """Translate landmarks from crop-relative [0,1] to original-frame [0,1]."""
    cx1, cy1, cx2, cy2 = crop_box
    cw = cx2 - cx1
    ch = cy2 - cy1
    out = []
    for lm in crop_landmarks:
        out.append(
            MappedLandmark(
                x=(cx1 + lm.x * cw) / orig_w,
                y=(cy1 + lm.y * ch) / orig_h,
                visibility=float(getattr(lm, "visibility", 1.0)),
            )
        )
    return out


def analyse(
    video_path: Path,
    out_dir: Path,
    model_variant: str = "lite",
    max_side: int = 1280,
    use_yolo: bool = True,
    yolo_conf: float = 0.25,
) -> dict:
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    model_path = ensure_model(model_variant)

    yolo_model = None
    if use_yolo:
        from ultralytics import YOLO  # Lazy import — heavy

        yolo_model = YOLO("yolov8n.pt")  # ~6 MB, auto-downloaded on first run

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cv2 could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_s = n_frames_total / fps if fps > 0 else 0.0

    annotated_path = out_dir / f"{video_path.stem}.annotated.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(annotated_path), fourcc, fps, (width, height))

    options = mp_vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        running_mode=mp_vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.4,
        min_pose_presence_confidence=0.4,
        min_tracking_confidence=0.4,
        output_segmentation_masks=False,
    )

    samples: list[FrameSample] = []
    frames_with_pose = 0
    frames_with_yolo_box = 0
    last_box: tuple[int, int, int, int] | None = None

    t_start = time.time()
    with mp_vision.PoseLandmarker.create_from_options(options) as landmarker:
        frame_idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = frame_idx / fps if fps > 0 else 0.0
            ts_ms = int(t * 1000)

            # Downscale a working copy for YOLO inference (faster).
            longer = max(width, height)
            if longer > max_side:
                scale = max_side / longer
                infer_w = int(round(width * scale))
                infer_h = int(round(height * scale))
                infer_frame = cv2.resize(
                    frame, (infer_w, infer_h), interpolation=cv2.INTER_AREA
                )
            else:
                infer_frame = frame
                scale = 1.0
                infer_w, infer_h = width, height

            # 1. Detect swimmer box (or fall back to previous frame's box).
            box_orig: tuple[int, int, int, int] | None = None
            if yolo_model is not None:
                yb = detect_swimmer_box(yolo_model, infer_frame, min_conf=yolo_conf)
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
                    box_orig = last_box  # carry forward last known
                if box_orig is not None:
                    frames_with_yolo_box += 1

            annotated = frame.copy()

            mapped: list[MappedLandmark] | None = None
            if box_orig is not None:
                # 2. Crop the original frame to the (padded) box for pose inference.
                crop_box = _expand_box(box_orig, width, height, pad_ratio=0.18)
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
                _draw_box(annotated, crop_box)
            else:
                # No YOLO: run pose on full downscaled frame
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

            if mapped is not None:
                frames_with_pose += 1
                samples.append(
                    FrameSample(
                        t=t,
                        nose=_xy(mapped[NOSE]),
                        l_shoulder=_xy(mapped[LEFT_SHOULDER]),
                        r_shoulder=_xy(mapped[RIGHT_SHOULDER]),
                        l_wrist=_xy(mapped[LEFT_WRIST]),
                        r_wrist=_xy(mapped[RIGHT_WRIST]),
                        l_hip=_xy(mapped[LEFT_HIP]),
                        r_hip=_xy(mapped[RIGHT_HIP]),
                    )
                )
                _draw_pose(annotated, mapped)
            else:
                samples.append(
                    FrameSample(
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

            writer.write(annotated)
            frame_idx += 1

    cap.release()
    writer.release()
    processing_seconds = time.time() - t_start

    metrics = _compute_metrics(samples, fps)
    n_frames = len(samples)
    pose_detection_rate = frames_with_pose / n_frames if n_frames else 0.0
    yolo_detection_rate = frames_with_yolo_box / n_frames if n_frames else 0.0

    return {
        "video": str(video_path),
        "annotated_video": str(annotated_path),
        "model_variant": model_variant,
        "use_yolo": use_yolo,
        "infer_max_side": max_side,
        "source_resolution": f"{width}x{height}",
        "duration_seconds": round(duration_s, 2),
        "fps": round(fps, 2),
        "frames_total": n_frames,
        "frames_with_pose": frames_with_pose,
        "pose_detection_rate": round(pose_detection_rate, 3),
        "yolo_detection_rate": round(yolo_detection_rate, 3),
        "processing_seconds": round(processing_seconds, 2),
        "realtime_ratio": (
            round(processing_seconds / duration_s, 2) if duration_s > 0 else None
        ),
        **metrics,
    }


def _compute_metrics(samples: list[FrameSample], fps: float) -> dict:
    if not samples:
        return {}
    duration_s = samples[-1].t - samples[0].t
    if duration_s <= 0:
        return {}

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

    min_distance = max(1, int(fps * 0.33))
    l_peaks = _local_peaks(l_track, min_distance)
    r_peaks = _local_peaks(r_track, min_distance)
    total_strokes = len(l_peaks) + len(r_peaks)
    stroke_rate_spm = (total_strokes / duration_s) * 60.0 if duration_s > 0 else 0.0

    # Body roll proxy with [0, 90] folding.
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/strokelab-out"))
    parser.add_argument(
        "--model",
        choices=["lite", "full", "heavy"],
        default="full",
        help="MediaPipe pose-landmarker variant (default: full)",
    )
    parser.add_argument(
        "--max-side",
        type=int,
        default=1280,
        help="Downscale longer side to this before YOLO inference",
    )
    parser.add_argument(
        "--no-yolo",
        action="store_true",
        help="Skip YOLO person detection; run pose on the whole frame",
    )
    parser.add_argument(
        "--yolo-conf",
        type=float,
        default=0.25,
        help="YOLO person-detection confidence threshold (default: 0.25)",
    )
    args = parser.parse_args(argv)

    report = analyse(
        args.video,
        args.out_dir,
        model_variant=args.model,
        max_side=args.max_side,
        use_yolo=not args.no_yolo,
        yolo_conf=args.yolo_conf,
    )
    report_path = args.out_dir / f"{args.video.stem}.report.json"
    report_path.write_text(json.dumps(report, indent=2))

    print(json.dumps(report, indent=2))
    print(f"\nAnnotated video: {report['annotated_video']}", file=sys.stderr)
    print(f"Report JSON:     {report_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
