"""Pose-based recovery counting — the deterministic counter that clears the 80%
segmentation gate (``validation/recovery_eval.py``).

The upper-box MOTION signal (``pipeline/track.py`` / ``segment.py``) caps at ~61%
within-±1 on the golden set: it's too coarse and folds in splash / head-bob /
far-arm noise. The WRIST keypoint is the actual recovery signal — a freestyle
over-water recovery is one clean arc of the camera-side wrist. Tracking the
near-arm wrist's height above its shoulder and prominence-peak-counting it scored
**87% within-±1 (20/23, golden normal+drills)** — even though YOLOv8-pose only
detects the (prone, half-submerged) swimmer in ~40% of frames; the detected
frames cluster on the recoveries and interpolation bridges the gaps.

Heavy deps (torch/ultralytics via the pose model) stay LAZY so this module
imports without them — the API service / CI never load it.
"""

from __future__ import annotations

# COCO keypoint indices emitted by yolov8-pose.
_LSHO, _RSHO, _LWRI, _RWRI = 5, 6, 9, 10

# Tuned on the golden set (validation/recovery_eval.py --method pose).
_MIN_CONF = 0.3  # ignore keypoints below this detector confidence
_MIN_PERIOD_S = 1.1  # a near-arm recovery rarely repeats faster than this
_SMOOTH = 5  # moving-average window on the wrist-height signal
_PROM_FRAC = 0.5  # a peak must rise this fraction of the signal range above its valleys


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


def wrist_recovery_signal(keypoints: list):
    """Near-arm wrist height above its shoulder, per frame (NaN-bridged). The near
    arm is the side with higher mean wrist confidence (the far arm is occluded by
    the body). y is image-down, so ``shoulder_y - wrist_y`` is positive when the
    wrist is ABOVE the shoulder — one peak per over-water recovery."""
    import numpy as np

    def conf(idx):
        cs = [kp[idx][2] for kp in keypoints if kp is not None]
        return float(np.mean(cs)) if cs else 0.0

    near_wri, near_sho = (
        (_LWRI, _LSHO) if conf(_LWRI) >= conf(_RWRI) else (_RWRI, _RSHO)
    )

    def col(idx, want):  # want: 0=x,1=y; gate on conf
        return np.array(
            [
                kp[idx][want]
                if (kp is not None and kp[idx][2] >= _MIN_CONF)
                else np.nan
                for kp in keypoints
            ],
            float,
        )

    wy, shy = _interp_nan(col(near_wri, 1)), _interp_nan(col(near_sho, 1))
    if wy is None or shy is None:
        return None
    return shy - wy


def count_recoveries(frames, timestamps) -> int:
    """Count over-water recoveries (== freestyle stroke cycles, near-arm 1:1) from
    a clip's frames via the pose wrist signal. Deterministic; needs torch (worker
    only). Returns 0 when pose can't be read."""
    import numpy as np

    from services.ai_service.pipeline.segment import _prominent_peaks

    sig = wrist_recovery_signal(_pose_keypoints(frames))
    if sig is None or len(sig) < 3:
        return 0
    if _SMOOTH > 1 and len(sig) >= _SMOOTH:
        sig = np.convolve(sig, np.ones(_SMOOTH) / _SMOOTH, mode="same")
    dt = float(np.median(np.diff(timestamps))) if len(timestamps) > 1 else 0.1
    min_dist = max(2, round(_MIN_PERIOD_S / dt))
    rng = float(sig.max() - sig.min()) or 1.0
    return len(_prominent_peaks(sig, min_dist, _PROM_FRAC * rng))
