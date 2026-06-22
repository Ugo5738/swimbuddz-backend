"""Swimmer-aware key-frame selection for the VLM coach.

Replaces the clock-uniform sampler in ``frames.py`` (which picks frames by the
clock, blind to content — so it grabbed tiny/far-swimmer frames and the VLM
false-refused them as "head-on"). Instead:

  1. detect + track the swimmer as a BOX (YOLO in prod; a cv2-motion fallback
     when torch/ultralytics is absent — also a no-GPU path for the small box),
  2. use the box to GATE and to spread frames — but, by default, EMIT THE FULL
     FRAME, not a tight crop. EMPIRICAL FINDING (Jun 2026): hard-cropping an
     elevated-deck angle removes the lane-line perspective the VLM uses to
     recognise "side-on", so a crop reads as "overhead" and gets refused. Keep
     the context; fix far/tiny swimmers by SELECTING the swimmer-large part of
     the clip instead. (emit="crop" stays available for level side views.)
  3. GATE OUT frames where the swimmer is too small to coach,
  4. SPREAD the kept frames across the arm cycle via a crop-restricted motion
     signal, so we don't waste frames on a glide/pause and we opportunistically
     catch recovery vs extension.

Box-only by design: we never trust in-water MediaPipe skeletons (land-trained,
unreliable in water). Reuses the proven ``detect_swimmer_box`` / ``_expand_box``
from ``coach/detect.py``. cv2/numpy are imported lazily so ``Frame`` /
``SelectionResult`` import fine in a cv2-less env.

CLI (extract swimmer-cropped frames + contact sheet)::

    python -m services.ai_service.coach.select \
        --clips a.mp4 b.mp4 --out /tmp/sl_crop --n 8 --detector motion
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from services.ai_service.coach.frames import Frame, _encode, _resize_long_edge

# A bbox in pixel coords of the working frame: (x1, y1, x2, y2)
Box = tuple[int, int, int, int]


@dataclass
class SelectionResult:
    """Selected frames plus the signals the worker/UX needs to stay honest."""

    frames: list[Frame]
    detector_used: str  # "yolo" | "motion" | "none"
    n_candidates: int
    n_kept: int
    too_small_to_coach: bool  # caller should short-circuit to a refusal + refund
    view_suspect: bool  # soft prior only — never a hard refusal
    fallback_used: bool
    notes: list[str] = field(default_factory=list)


# ── swimmer detection ────────────────────────────────────────────────


def _detect_boxes_yolo(frames: list) -> list[Optional[Box]]:
    """Proven path: YOLOv8n person detection + the swimmer-box heuristic."""
    from services.ai_service.coach.detect import (
        detect_swimmer_box,
        load_yolo_model,
    )

    model = load_yolo_model()
    return [detect_swimmer_box(model, f, min_conf=0.25) for f in frames]


def _detect_boxes_motion(frames: list) -> list[Optional[Box]]:
    """No-torch fallback: largest coherent moving blob vs a median background.

    Good enough for a static-camera pool clip — the swimmer is the dominant
    mover. Generous box padding downstream tolerates a slightly-off blob.
    """
    import cv2
    import numpy as np

    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    bg = np.median(np.stack(grays), axis=0).astype("uint8")
    boxes: list[Optional[Box]] = []
    fh, fw = grays[0].shape[:2]
    min_blob = 0.01 * fw * fh
    for g in grays:
        diff = cv2.absdiff(g, bg)
        diff = cv2.GaussianBlur(diff, (5, 5), 0)
        _, mask = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE, np.ones((9, 9), "uint8"), iterations=2
        )
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cnts = [c for c in cnts if cv2.contourArea(c) >= min_blob]
        if not cnts:
            boxes.append(None)
            continue
        x, y, w, h = cv2.boundingRect(max(cnts, key=cv2.contourArea))
        boxes.append((x, y, x + w, y + h))
    return boxes


def _fill_boxes(boxes: list[Optional[Box]]) -> list[Optional[Box]]:
    """Carry-forward across single-frame misses (same idea pose_pipeline uses)."""
    out: list[Optional[Box]] = list(boxes)
    last: Optional[Box] = None
    for i, b in enumerate(out):
        if b is None:
            out[i] = last
        else:
            last = b
    # back-fill any leading None with the first real box
    first = next((b for b in out if b is not None), None)
    return [b if b is not None else first for b in out]


# ── selection ────────────────────────────────────────────────────────


def select_frames(
    clip_path: str | Path,
    n_frames: int = 8,
    detector: str = "auto",  # "auto" | "yolo" | "motion"
    max_edge: int = 768,
    min_area_frac: float = 0.04,
    pad_ratio: float = 0.32,
    n_candidates: int = 24,
    work_long_edge: int = 720,
    jpeg_quality: int = 80,
    emit: str = "full",  # "full" keeps lane context (recommended); "crop" zooms to the box
) -> SelectionResult:
    """Pick ``n_frames`` swimmer-cropped, motion-spread frames from the clip."""
    import cv2
    import numpy as np

    from services.ai_service.coach.detect import _expand_box

    notes: list[str] = []

    # 1. decode evenly-spaced candidate frames at working resolution
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {clip_path}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cand_idx = (
        np.linspace(int(total * 0.02), int(total * 0.98), n_candidates).astype(int)
        if total > 0
        else np.arange(n_candidates)
    )
    cand: list[tuple[float, "np.ndarray"]] = []
    for fidx in cand_idx:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(fidx))
        ok, img = cap.read()
        if ok and img is not None:
            cand.append((float(fidx) / fps, _resize_long_edge(img, work_long_edge)))
    cap.release()
    if not cand:
        raise RuntimeError(f"no frames decoded from {clip_path}")
    frames_bgr = [c[1] for c in cand]
    times = [c[0] for c in cand]
    fh, fw = frames_bgr[0].shape[:2]

    # 2. detect swimmer boxes (YOLO if available, else motion fallback)
    fallback_used = False
    det = detector
    if det in ("auto", "yolo"):
        try:
            boxes = _detect_boxes_yolo(frames_bgr)
            det = "yolo"
        except Exception as exc:  # torch/ultralytics absent or model load failed
            if detector == "yolo":
                raise
            notes.append(f"yolo unavailable ({type(exc).__name__}); using motion")
            boxes = _detect_boxes_motion(frames_bgr)
            det, fallback_used = "motion", True
    else:
        boxes = _detect_boxes_motion(frames_bgr)
        det = "motion"
    boxes = _fill_boxes(boxes)
    if all(b is None for b in boxes):
        return SelectionResult(
            [], "none", len(cand), 0, True, False, fallback_used, notes
        )

    # 3. area fraction per candidate; 4. gate out too-small frames
    frame_area = float(fw * fh)
    kept: list[int] = []
    for i, b in enumerate(boxes):
        if b is None:
            continue
        x1, y1, x2, y2 = b
        if (x2 - x1) * (y2 - y1) / frame_area >= min_area_frac:
            kept.append(i)
    if len(kept) < min(3, n_frames):
        notes.append(f"only {len(kept)} well-framed candidates (swimmer too small/far)")
        return SelectionResult(
            [], det, len(cand), len(kept), True, False, fallback_used, notes
        )

    # 5. crop each kept candidate to the padded box, downscale, encode
    crops: dict[int, "np.ndarray"] = {}
    for i in kept:
        ex = _expand_box(boxes[i], fw, fh, pad_ratio)
        x1, y1, x2, y2 = ex
        crop = frames_bgr[i][y1:y2, x1:x2]
        if crop.size:
            crops[i] = _resize_long_edge(crop, max_edge)

    # 6. crop-restricted motion signal S(t): abs-diff of consecutive crops,
    #    upper half (where an over-water recovery spikes). Resize to a common
    #    small grid so frames of differing crop size are comparable.
    import numpy as np

    g = 64
    small = {
        i: cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), (g, g))[: g // 2, :].astype(
            "float32"
        )
        for i, c in crops.items()
    }
    s_signal: dict[int, float] = {}
    prev = None
    for i in kept:
        cur = small.get(i)
        if cur is None:
            continue
        s_signal[i] = 0.0 if prev is None else float(np.abs(cur - prev).mean())
        prev = cur

    # 7. spread: farthest-point sampling in (normalized time, normalized motion)
    #    so we cover the arm cycle (recovery≈high S, glide≈low S) AND the clip.
    usable = [i for i in kept if i in crops]
    if not usable:
        return SelectionResult(
            [], det, len(cand), len(kept), True, False, fallback_used, notes
        )
    tmin, tmax = times[usable[0]], times[usable[-1]]
    smin = min(s_signal.get(i, 0.0) for i in usable)
    smax = max(s_signal.get(i, 0.0) for i in usable)

    def _pt(i: int) -> tuple[float, float]:
        tn = (times[i] - tmin) / (tmax - tmin) if tmax > tmin else 0.0
        sn = (s_signal.get(i, 0.0) - smin) / (smax - smin) if smax > smin else 0.0
        return tn, sn

    picked = [max(usable, key=lambda i: s_signal.get(i, 0.0))]  # seed: most motion
    while len(picked) < min(n_frames, len(usable)):
        best, best_d = None, -1.0
        for i in usable:
            if i in picked:
                continue
            pi = _pt(i)
            d = min((pi[0] - _pt(j)[0]) ** 2 + (pi[1] - _pt(j)[1]) ** 2 for j in picked)
            if d > best_d:
                best, best_d = i, d
        if best is None:
            break
        picked.append(best)
    picked.sort()  # chronological for the VLM

    # 8. soft side-on prior (never a hard refusal): a swimmer travelling across
    #    the frame has wide horizontal span; one that mostly grows/shrinks (low
    #    span, high area drift) is suspect (head-on / swimming at the camera).
    cxs = [(boxes[i][0] + boxes[i][2]) / 2 / fw for i in kept]
    areas = [
        (boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]) / frame_area
        for i in kept
    ]
    h_span = (max(cxs) - min(cxs)) if cxs else 0.0
    area_drift = (max(areas) - min(areas)) if areas else 0.0
    view_suspect = h_span < 0.15 and area_drift > 0.10

    out_frames = []
    for k, i in enumerate(picked):
        # Default to the full (gate-passed) frame for view context; the box still
        # did its job by dropping far frames and spreading across the arm cycle.
        img = crops[i] if emit == "crop" else _resize_long_edge(frames_bgr[i], max_edge)
        out_frames.append(Frame(k, times[i], _encode(img, jpeg_quality)))
    return SelectionResult(
        frames=out_frames,
        detector_used=det,
        n_candidates=len(cand),
        n_kept=len(kept),
        too_small_to_coach=False,
        view_suspect=view_suspect,
        fallback_used=fallback_used,
        notes=notes,
    )


def _main() -> int:
    import cv2

    from services.ai_service.coach.frames import build_montage, save_frames

    ap = argparse.ArgumentParser(description="Swimmer-aware key-frame selection")
    ap.add_argument("--clips", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--detector", default="auto", choices=["auto", "yolo", "motion"])
    args = ap.parse_args()

    root = Path(args.out)
    for clip in args.clips:
        name = Path(clip).stem
        res = select_frames(clip, n_frames=args.n, detector=args.detector)
        if res.too_small_to_coach or not res.frames:
            print(
                f"{name}: REFUSE (too small/far) — detector={res.detector_used} "
                f"kept={res.n_kept}/{res.n_candidates} {res.notes}"
            )
            continue
        save_frames(res.frames, root / name)
        cv2.imwrite(str(root / f"{name}__montage.jpg"), build_montage(res.frames))
        print(
            f"{name}: {len(res.frames)} frames | detector={res.detector_used} "
            f"kept={res.n_kept}/{res.n_candidates} view_suspect={res.view_suspect} "
            f"fallback={res.fallback_used} {res.notes}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
