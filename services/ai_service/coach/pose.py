"""Pose-based recovery counting — the deterministic counter behind the per-stroke
drilldown.

Two-clip validation (test2/test3, Jun 2026, against pose-signal + a 24-agent
vision cross-count) showed the original global-``range`` prominence gate is
fragile in two distinct ways:

  * THRESHOLD — a single near-camera high-amplitude recovery inflates the global
    range, and the ``0.5·range`` gate then drops every normal recovery further
    down the lane (test2 counted **2** for a verified ~17-stroke lap).
  * DETECTION — it can't tell "few strokes" from "pose never saw the swimmer".
    seg58 (25% detection, near-wrist conf 0.27) yields only 8 candidate peaks for
    14 real strokes — unrecoverable by any threshold.

This version fixes both:

  * a ROBUST prominence threshold (``k·MAD``, immune to outlier amplitudes) —
    test2 2→17 exact; MAE on counted long clips 4.86→0.67;
  * a DETECTION GATE — when yolov8-pose finds the swimmer in too few frames, or
    the near wrist is too low-confidence, ``count_recoveries`` REFUSES a precise
    count (``RecoveryResult.count is None``) instead of emitting a wild number.
    The per-stroke drilldown is gated on this confidence.

Honest precision: ±1–2 on good-detection side-on freestyle laps; abstains on
poor-detection clips. NOT a precise counter on hard in-water footage (that needs
a better/aquatic pose backbone).

Heavy deps (torch/ultralytics via the pose model) stay LAZY so this module
imports without them — the API service / CI never load it.
"""

from __future__ import annotations

from dataclasses import dataclass

# COCO keypoint indices emitted by yolov8-pose.
_LSHO, _RSHO, _LWRI, _RWRI = 5, 6, 9, 10

# Tuned/validated on the golden set + test2/test3 (validation/recovery_eval.py).
_MIN_CONF = 0.3  # ignore individual keypoints below this detector confidence
_MIN_PERIOD_S = 1.1  # a near-arm recovery rarely repeats faster than this
_SMOOTH = 5  # moving-average window on the wrist-height signal
_PROM_K_MAD = 1.5  # a peak must rise this many MADs above its valleys (robust scale)

# Detection gate — below either floor we can't count reliably, so we refuse.
_GATE_DET_RATE = 0.5  # pose must find the swimmer in >= this fraction of frames
_GATE_WRIST_CONF = 0.3  # median near-wrist confidence must be >= this


@dataclass(frozen=True)
class RecoveryResult:
    """Outcome of a recovery count. ``count`` is None when the detection gate
    refuses (pose too sparse / too weak to count) — callers should hide the
    per-stroke drilldown and fall back to non-numbered feedback."""

    count: int | None
    confidence: str  # "ok" | "low_detection" | "unreadable"
    detection_rate: float
    near_wrist_conf: float
    peaks_s: tuple[float, ...] = ()  # absolute time of each recovery peak — the
    # segmentation the pose_count component turns into near-arm recovery instances

    @property
    def refused(self) -> bool:
        return self.count is None


def _pose_keypoints(frames) -> list:
    """Run yolov8-pose over the frames → per-frame (17,3) keypoint array (x,y,conf)
    for the highest-confidence person, or None when no swimmer is found."""
    import numpy as np
    from ultralytics import YOLO

    model = YOLO("yolov8n-pose.pt")
    out = []
    for r in model(frames, verbose=False, imgsz=640):
        kp = r.keypoints
        if kp is None or kp.data.shape[0] == 0:
            out.append(None)
            continue
        bi = 0
        if r.boxes is not None and len(r.boxes):
            bi = int(np.argmax(r.boxes.conf.cpu().numpy()))
        out.append(kp.data[bi].cpu().numpy())
    return out


def _interp_nan(a):
    import numpy as np

    idx = np.arange(len(a))
    good = ~np.isnan(a)
    if good.sum() < 2:
        return None
    return np.interp(idx, idx[good], a[good])


def _near_arm(keypoints) -> tuple[int, int]:
    """Pick the camera-side (near) arm = the side with higher mean wrist
    confidence (the far arm is occluded by the body). Returns
    (wrist_idx, shoulder_idx)."""
    import numpy as np

    def mean_conf(idx):
        cs = [kp[idx][2] for kp in keypoints if kp is not None]
        return float(np.mean(cs)) if cs else 0.0

    if mean_conf(_LWRI) >= mean_conf(_RWRI):
        return _LWRI, _LSHO
    return _RWRI, _RSHO


def _median_conf(keypoints, idx) -> float:
    import numpy as np

    cs = [kp[idx][2] for kp in keypoints if kp is not None]
    return float(np.median(cs)) if cs else 0.0


def wrist_recovery_signal(keypoints: list):
    """Near-arm wrist height above its shoulder, per frame (NaN-bridged). y is
    image-down, so ``shoulder_y - wrist_y`` is positive when the wrist is ABOVE
    the shoulder — one peak per over-water recovery."""
    near_wri, near_sho = _near_arm(keypoints)
    return _signal_for_arm(keypoints, near_wri, near_sho)


def _signal_for_arm(keypoints, wri_idx, sho_idx):
    import numpy as np

    def col(idx, want):  # want: 0=x,1=y; gate on per-keypoint conf
        return np.array(
            [
                kp[idx][want]
                if (kp is not None and kp[idx][2] >= _MIN_CONF)
                else np.nan
                for kp in keypoints
            ],
            float,
        )

    wy, shy = _interp_nan(col(wri_idx, 1)), _interp_nan(col(sho_idx, 1))
    if wy is None or shy is None:
        return None
    return shy - wy


def count_recoveries(frames, timestamps) -> RecoveryResult:
    """Count over-water recoveries (== freestyle stroke cycles, near-arm 1:1) from
    a clip's frames via the pose wrist signal. Deterministic; needs torch (worker
    only).

    Applies the detection gate: returns ``RecoveryResult(count=None, ...)`` when
    pose detection is too sparse / too weak to count reliably (the drilldown is
    then suppressed). Otherwise counts via a robust ``k·MAD`` prominence so a few
    near-camera high-amplitude recoveries can't suppress the rest, and returns the
    per-recovery PEAK TIMES (``peaks_s``) — the segmentation, not just the tally."""
    import numpy as np

    from services.ai_service.pipeline.segment import _prominent_peaks

    keypoints = _pose_keypoints(frames)
    n_frames = len(keypoints) or 1
    det_rate = sum(1 for kp in keypoints if kp is not None) / n_frames
    near_wri, near_sho = _near_arm(keypoints)
    near_conf = _median_conf(keypoints, near_wri)

    sig = _signal_for_arm(keypoints, near_wri, near_sho)
    if sig is None or len(sig) < 3:
        return RecoveryResult(None, "unreadable", det_rate, near_conf)
    if det_rate < _GATE_DET_RATE or near_conf < _GATE_WRIST_CONF:
        return RecoveryResult(None, "low_detection", det_rate, near_conf)

    if _SMOOTH > 1 and len(sig) >= _SMOOTH:
        sig = np.convolve(sig, np.ones(_SMOOTH) / _SMOOTH, mode="same")
    dt = float(np.median(np.diff(timestamps))) if len(timestamps) > 1 else 0.1
    min_dist = max(2, round(_MIN_PERIOD_S / dt))
    mad = float(np.median(np.abs(sig - np.median(sig)))) or 1.0
    peak_idxs = _prominent_peaks(sig, min_dist, _PROM_K_MAD * mad)
    peaks_s = tuple(
        round(float(timestamps[i]), 3) for i in peak_idxs if 0 <= i < len(timestamps)
    )
    return RecoveryResult(len(peaks_s), "ok", det_rate, near_conf, peaks_s)
