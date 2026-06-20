"""Key-frame selection for the Stroke Lab VLM coach.

The whole cost story of the coach rests on this file: we never send a video to
the model, only a handful of downscaled still frames. Frame count and
resolution are the two cost knobs.

Imports only OpenCV + numpy (no LiteLLM), so it runs in any environment with
cv2 — including the dev machine's system Python where the LLM stack is absent.

CLI (extract + contact sheet for inspection)::

    python -m services.ai_service.coach.frames \
        --clips a.mp4 b.mp4 --out /tmp/sl_coach --n 8
"""

from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from pathlib import Path

# NOTE: cv2/numpy are imported lazily inside the functions that need them, so
# `Frame` and `load_frames` can be imported in an environment without OpenCV
# (e.g. the dev venv that has LiteLLM but not cv2). See module docstring.


@dataclass
class Frame:
    """One sampled, JPEG-encoded frame plus where it came from."""

    index: int
    timestamp_s: float
    jpeg: bytes


def _resize_long_edge(img, max_edge: int):
    import cv2

    h, w = img.shape[:2]
    scale = max_edge / float(max(h, w))
    if scale < 1.0:
        img = cv2.resize(
            img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
        )
    return img


def _encode(img, quality: int) -> bytes:
    import cv2

    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


def extract_key_frames(
    clip_path: str | Path,
    n_frames: int = 8,
    max_edge: int = 768,
    jpeg_quality: int = 80,
) -> list[Frame]:
    """Sample ``n_frames`` frames evenly across the middle of the clip.

    We skip the first/last 5% to dodge cut-in/out edges. This is a deliberately
    simple v0 selector — motion/pose-keyed selection (catch, recovery, breath)
    is a later upgrade; uniform sampling is honest and cheap to start.
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {clip_path}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

    frames: list[Frame] = []
    if total > 0:
        lo, hi = int(total * 0.05), int(total * 0.95)
        hi = max(hi, lo + 1)
        targets = np.linspace(lo, hi, n_frames).astype(int)
        for i, fidx in enumerate(targets):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(fidx))
            ok, img = cap.read()
            if not ok or img is None:
                continue
            frames.append(
                Frame(
                    i,
                    float(fidx) / fps,
                    _encode(_resize_long_edge(img, max_edge), jpeg_quality),
                )
            )
    if not frames:
        # Fallback: stream all frames, keep n evenly spaced (robust to bad metadata).
        all_imgs: list[np.ndarray] = []
        while True:
            ok, img = cap.read()
            if not ok or img is None:
                break
            all_imgs.append(img)
        if not all_imgs:
            cap.release()
            raise RuntimeError(f"no frames decoded from {clip_path}")
        idxs = np.linspace(0, len(all_imgs) - 1, n_frames).astype(int)
        for i, fidx in enumerate(idxs):
            frames.append(
                Frame(
                    i,
                    float(fidx) / fps,
                    _encode(_resize_long_edge(all_imgs[fidx], max_edge), jpeg_quality),
                )
            )
    cap.release()
    return frames


def save_frames(frames: list[Frame], out_dir: str | Path) -> Path:
    """Write frames to ``out_dir`` as ``frame_<idx>_t<seconds>.jpg``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for f in frames:
        (out / f"frame_{f.index:02d}_t{f.timestamp_s:06.2f}.jpg").write_bytes(f.jpeg)
    return out


_FNAME_RE = re.compile(r"frame_(\d+)_t([\d.]+)\.jpg$")


def load_frames(frames_dir: str | Path) -> list[Frame]:
    """Reload frames previously written by :func:`save_frames`.

    Lets the model-call step (which needs LiteLLM but not cv2) run in a
    different Python than the extraction step.
    """
    out = Path(frames_dir)
    items: list[Frame] = []
    for p in sorted(out.glob("frame_*.jpg")):
        m = _FNAME_RE.search(p.name)
        idx = int(m.group(1)) if m else len(items)
        ts = float(m.group(2)) if m else float(len(items))
        items.append(Frame(idx, ts, p.read_bytes()))
    return items


def build_montage(frames: list[Frame], cols: int = 4, cell_w: int = 320):
    """Labeled contact sheet of the frames, for human/Claude inspection."""
    import cv2
    import numpy as np

    pad, bg = 6, 20
    rows = math.ceil(len(frames) / cols)
    cells, cell_h = [], 0
    for f in frames:
        arr = cv2.imdecode(np.frombuffer(f.jpeg, np.uint8), cv2.IMREAD_COLOR)
        h, w = arr.shape[:2]
        arr = cv2.resize(arr, (cell_w, int(h * cell_w / w)))
        cv2.putText(
            arr,
            f"#{f.index} {f.timestamp_s:.1f}s",
            (8, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        cells.append(arr)
        cell_h = max(cell_h, arr.shape[0])
    canvas = np.full(
        (rows * (cell_h + pad) + pad, cols * (cell_w + pad) + pad, 3), bg, np.uint8
    )
    for i, c in enumerate(cells):
        r, cc = divmod(i, cols)
        y, x = pad + r * (cell_h + pad), pad + cc * (cell_w + pad)
        canvas[y : y + c.shape[0], x : x + cell_w] = c
    return canvas


def _main() -> int:
    import cv2

    ap = argparse.ArgumentParser(description="Extract key frames + contact sheets")
    ap.add_argument("--clips", nargs="+", required=True)
    ap.add_argument(
        "--out", required=True, help="root output dir (one subdir per clip)"
    )
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--max-edge", type=int, default=768)
    ap.add_argument("--quality", type=int, default=80)
    args = ap.parse_args()

    root = Path(args.out)
    for clip in args.clips:
        name = Path(clip).stem
        frames = extract_key_frames(clip, args.n, args.max_edge, args.quality)
        d = save_frames(frames, root / name)
        cv2.imwrite(str(root / f"{name}__montage.jpg"), build_montage(frames))
        total_kb = sum(len(f.jpeg) for f in frames) / 1024
        print(f"{name}: {len(frames)} frames -> {d} ({total_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
