"""Branded share-card rendering for Stroke Lab findings (prototype).

Each coaching finding → a shareable image: the evidence frame as the background,
the fault as the headline, the analysis area, a timestamp, and SwimBuddz branding.
Pure cv2 (no browser/canvas), so the worker — which already has cv2 and the
selected frames — can render these server-side. One template, parameterised by
the closed-enum ``area``; that keeps every card on-brand with zero per-result
layout work, and each card is a self-contained TikTok/Status unit.

cv2's built-in (Hershey) fonts are functional, not beautiful — a later polish
pass can swap in PIL + a brand TTF. CLI::

    python -m services.ai_service.coach.cards --frame f.jpg \
        --fault "Head too high, legs drop" --area head_breath --t 3.1 --out card.jpg
"""

from __future__ import annotations

import argparse
from pathlib import Path

# Closed-enum area → human label. Mirrors the coach schema's `area` field.
AREA_LABELS = {
    "body_line": "BODY LINE",
    "recovery_elbow": "RECOVERY & ELBOW",
    "head_breath": "HEAD & BREATHING",
    "entry_reach": "ENTRY & REACH",
    "catch_pull": "CATCH & PULL",
    "kick": "KICK",
    "other": "TECHNIQUE",
}
ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
_CYAN = (224, 196, 0)  # BGR — SwimBuddz cyan accent


def _ascii(s: str) -> str:
    """cv2 Hershey fonts are ASCII-only; map the punctuation LLMs love to ASCII."""
    for k, v in {
        "—": "-",
        "–": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        "°": " deg",
    }.items():
        s = s.replace(k, v)
    return s.encode("ascii", "ignore").decode()


def _cover(img, w: int, h: int):
    """Scale + center-crop ``img`` to exactly w×h (CSS background:cover)."""
    import cv2

    ih, iw = img.shape[:2]
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale) + 1, int(ih * scale) + 1
    r = cv2.resize(img, (nw, nh))
    x, y = (nw - w) // 2, (nh - h) // 2
    return r[y : y + h, x : x + w]


def _wrap(text: str, font, scale: float, thick: int, max_w: int) -> list[str]:
    import cv2

    words, lines, cur = text.split(), [], ""
    for wd in words:
        trial = f"{cur} {wd}".strip()
        if cv2.getTextSize(trial, font, scale, thick)[0][0] <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def render_share_card(
    evidence_jpeg: bytes,
    fault: str,
    *,
    area: str = "other",
    timestamp_s: float | None = None,
    aspect: str = "9:16",
    jpeg_quality: int = 85,
) -> bytes:
    """Render one branded share card. Returns JPEG bytes."""
    import cv2
    import numpy as np

    w, h = ASPECTS.get(aspect, ASPECTS["9:16"])
    img = cv2.imdecode(np.frombuffer(evidence_jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode evidence frame")
    fault = _ascii(fault)
    canvas = (_cover(img, w, h).astype("float32") * 0.85).astype("uint8")

    # bottom-up dark gradient so headline text stays legible over any frame
    grad_h = int(h * 0.46)
    for i in range(grad_h):
        a = (i / grad_h) ** 1.4 * 0.9
        y = h - grad_h + i
        canvas[y] = (canvas[y].astype("float32") * (1 - a)).astype("uint8")

    font = cv2.FONT_HERSHEY_DUPLEX
    m = int(w * 0.06)  # margin

    # area pill (top-left)
    label = AREA_LABELS.get(area, AREA_LABELS["other"])
    (lw, lh), _ = cv2.getTextSize(label, font, 0.9, 2)
    cv2.rectangle(canvas, (m - 14, m - 8), (m + lw + 14, m + lh + 18), _CYAN, -1)
    cv2.putText(canvas, label, (m, m + lh + 4), font, 0.9, (30, 30, 30), 2, cv2.LINE_AA)

    # timestamp (top-right)
    if timestamp_s is not None:
        ts = f"t={timestamp_s:.1f}s"
        (tw, th), _ = cv2.getTextSize(ts, font, 0.8, 2)
        cv2.putText(
            canvas, ts, (w - m - tw, m + th), font, 0.8, (255, 255, 255), 2, cv2.LINE_AA
        )

    # headline (wrapped), drawn bottom-up above the brand line
    hl_scale, hl_thick = 1.7, 3
    lines = _wrap(fault, font, hl_scale, hl_thick, w - 2 * m)
    line_h = cv2.getTextSize("Ag", font, hl_scale, hl_thick)[0][1] + 26
    brand_y = h - int(m * 1.2)
    y = brand_y - line_h - 24
    for ln in reversed(lines):
        cv2.putText(
            canvas, ln, (m, y), font, hl_scale, (255, 255, 255), hl_thick, cv2.LINE_AA
        )
        y -= line_h

    # brand line
    cv2.line(canvas, (m, brand_y - 40), (w - m, brand_y - 40), _CYAN, 2)
    cv2.putText(
        canvas,
        "Stroke Lab  .  SwimBuddz",
        (m, brand_y),
        font,
        0.85,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    url = "analyzer.swimbuddz.com"
    (uw, _), _ = cv2.getTextSize(url, font, 0.7, 1)
    cv2.putText(canvas, url, (w - m - uw, brand_y), font, 0.7, _CYAN, 1, cv2.LINE_AA)

    ok, buf = cv2.imencode(
        ".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
    )
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return buf.tobytes()


def _main() -> int:
    ap = argparse.ArgumentParser(description="Render a Stroke Lab share card")
    ap.add_argument("--frame", required=True, help="evidence frame jpg")
    ap.add_argument("--fault", required=True)
    ap.add_argument("--area", default="other", choices=list(AREA_LABELS))
    ap.add_argument("--t", type=float, default=None)
    ap.add_argument("--aspect", default="9:16", choices=list(ASPECTS))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    card = render_share_card(
        Path(args.frame).read_bytes(),
        args.fault,
        area=args.area,
        timestamp_s=args.t,
        aspect=args.aspect,
    )
    Path(args.out).write_bytes(card)
    print(f"wrote {args.out} ({len(card) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
