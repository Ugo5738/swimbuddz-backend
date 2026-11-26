"""SwimBuddz Apache-2.0 pose recovery — served on Modal without AGPL ultralytics.

Drop-in commercial-safe replacement for ``pose_modal.py`` (which uses AGPL
``ultralytics`` + ``yolov8n-pose.pt`` + PyTorch). Runs OpenMMLab's Apache-2.0
``rtmlib`` + ``onnxruntime-gpu`` (RTMPose balanced, COCO-17) over densely
decoded frames and returns the exact same JSON contract expected by
``services/ai_service/pipeline/components/pose_recovery.py::_modal_count``::

    POST <url>?max_frames=300  body=<raw video bytes>  headers: Modal-Key/Secret
    -> {"ok": true, "count": int | null, "peaks_s": [...], "detection_rate": ...,
        "near_wrist_conf": ..., "confidence": ..., "refused": bool, "engine": "rtmpose"}

Keep the MAD wrist-recovery peak counter in sync with
``services/ai_service/coach/pose.py``.

SETUP (one-time, on your machine):
    pip install modal
    modal token new
    modal deploy services/ai_service/modal/pose_modal_rtmpose.py

Then point the worker at the new URL:
    STROKELAB_POSE_BACKEND=modal
    STROKELAB_POSE_MODAL_URL=<printed url>
    STROKELAB_POSE_MODAL_KEY=<key>
    STROKELAB_POSE_MODAL_SECRET=<secret>
"""

import modal
from fastapi import Request

# Lightweight Apache-2.0 ONNX image (no PyTorch, no AGPL ultralytics).
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install(
        "rtmlib>=0.0.13,<1.0",
        "onnxruntime-gpu>=1.19,<2.0",
        "opencv-python-headless>=4.10,<5.0",
        "numpy>=1.26,<3.0",
        "fastapi[standard]",
    )
    .run_commands(
        # Pre-download RTMPose balanced ONNX weights into the image cache.
        "python -c \"from rtmlib import Body; Body(mode='balanced', backend='onnxruntime', device='cpu')\"",
    )
)

app = modal.App("swimbuddz-pose-rtmpose", image=image)

# ── Pose counter (mirrors coach/pose.py + segment._prominent_peaks — KEEP IN SYNC)
_LSHO, _RSHO, _LWRI, _RWRI = 5, 6, 9, 10
_MIN_CONF, _MIN_PERIOD_S, _SMOOTH, _PROM_K_MAD = 0.3, 1.1, 5, 1.5
_GATE_DET_RATE, _GATE_WRIST_CONF = 0.5, 0.3


def _decode_dense(path, stride=2, max_frames=300, long_edge=720):
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


def _prominent_peaks(sig, min_dist, min_prom):
    n = len(sig)
    cand = sorted(
        (i for i in range(1, n - 1) if sig[i] >= sig[i - 1] and sig[i] >= sig[i + 1]),
        key=lambda i: -sig[i],
    )
    chosen = []
    for i in cand:
        if all(abs(i - j) >= min_dist for j in chosen):
            chosen.append(i)
    chosen.sort()
    out = []
    for k, i in enumerate(chosen):
        lo = chosen[k - 1] if k > 0 else 0
        hi = chosen[k + 1] if k < len(chosen) - 1 else n - 1
        valley = min(float(sig[lo : i + 1].min()), float(sig[i : hi + 1].min()))
        if sig[i] - valley >= min_prom:
            out.append(i)
    return out


def _pose_keypoints(frames):
    import numpy as np
    from rtmlib import Body

    body = Body(mode="balanced", backend="onnxruntime", device="cuda")
    out = []
    for frame in frames:
        kps, scores = body(frame)
        if kps is None or len(kps) == 0:
            out.append(None)
            continue
        kps_arr = np.asarray(kps, dtype=float)
        scores_arr = np.asarray(scores, dtype=float)
        if kps_arr.ndim != 3 or kps_arr.shape[0] == 0:
            out.append(None)
            continue
        bi = int(np.argmax(np.mean(scores_arr, axis=1)))
        out.append(np.concatenate([kps_arr[bi], scores_arr[bi][:, None]], axis=-1))
    return out


def _count(frames, times):
    import numpy as np

    kps = _pose_keypoints(frames)
    n = len(kps) or 1
    det = sum(1 for k in kps if k is not None) / n

    def mean_conf(idx):
        cs = [k[idx][2] for k in kps if k is not None]
        return float(np.mean(cs)) if cs else 0.0

    near_wri, near_sho = (
        (_LWRI, _LSHO) if mean_conf(_LWRI) >= mean_conf(_RWRI) else (_RWRI, _RSHO)
    )
    wconf = [k[near_wri][2] for k in kps if k is not None]
    near_conf = float(np.median(wconf)) if wconf else 0.0

    def col(idx):
        a = np.array(
            [
                k[idx][1] if (k is not None and k[idx][2] >= _MIN_CONF) else np.nan
                for k in kps
            ],
            float,
        )
        good = ~np.isnan(a)
        return (
            np.interp(np.arange(len(a)), np.arange(len(a))[good], a[good])
            if good.sum() >= 2
            else None
        )

    wy, shy = col(near_wri), col(near_sho)
    if wy is None or shy is None:
        return {
            "ok": True,
            "count": None,
            "confidence": "unreadable",
            "detection_rate": det,
            "near_wrist_conf": near_conf,
            "refused": True,
            "peaks_s": [],
            "engine": "rtmpose",
        }
    if det < _GATE_DET_RATE or near_conf < _GATE_WRIST_CONF:
        return {
            "ok": True,
            "count": None,
            "confidence": "low_detection",
            "detection_rate": det,
            "near_wrist_conf": near_conf,
            "refused": True,
            "peaks_s": [],
            "engine": "rtmpose",
        }
    sig = shy - wy
    sig = np.convolve(sig, np.ones(_SMOOTH) / _SMOOTH, mode="same")
    dt = float(np.median(np.diff(times))) if len(times) > 1 else 0.1
    mad = float(np.median(np.abs(sig - np.median(sig)))) or 1.0
    peaks = _prominent_peaks(sig, max(2, round(_MIN_PERIOD_S / dt)), _PROM_K_MAD * mad)
    peaks_s = [round(float(times[i]), 3) for i in peaks if 0 <= i < len(times)]
    return {
        "ok": True,
        "count": len(peaks_s),
        "confidence": "ok",
        "detection_rate": det,
        "near_wrist_conf": near_conf,
        "refused": False,
        "peaks_s": peaks_s,
        "engine": "rtmpose",
    }


@app.function(gpu="T4", timeout=300)
@modal.fastapi_endpoint(method="POST", requires_proxy_auth=True)
async def count(request: Request):
    """POST the raw video bytes; ?max_frames=N. Returns the recovery count JSON."""
    import tempfile

    max_frames = int(request.query_params.get("max_frames", "300"))
    body = await request.body()
    if not body:
        return {"ok": False, "reason": "empty_body"}
    with tempfile.NamedTemporaryFile(suffix=".mp4") as fh:
        fh.write(body)
        fh.flush()
        frames, times = _decode_dense(fh.name, max_frames=max_frames)
    if not frames:
        return {"ok": False, "reason": "no_frames"}
    return _count(frames, times)
