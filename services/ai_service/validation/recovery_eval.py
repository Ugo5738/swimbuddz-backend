"""Validate Stage-1 recovery segmentation against the golden labels.

The instance UX (count recoveries, drill into each) is only trustworthy if the
detector counts recoveries reliably. CONVENTION (verified across all 27 labeled
clips, where len(recovery_times) == stroke_cycles, and confirmed by the panel):
the segmenter tracks the NEAR (camera-side) arm, so expected recoveries =
stroke_cycles (1:1) — NOT 2×. (1 cycle = 1 near-arm recovery; the labeler
recorded only near-arm events.) We report detected vs expected per clip + MAE +
within-±1 rate. Pure local CV (no API).

    python -m services.ai_service.validation.recovery_eval \
        --golden-root ~/Downloads/strokelab2/golden [--category normal] [--detector motion]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path


def _load_key(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    lines = [
        ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("#")
    ]
    out: dict[str, int] = {}
    for r in csv.DictReader(lines):
        fn = (r.get("file") or "").strip()
        cyc = (r.get("stroke_cycles") or "").strip()
        if fn and cyc.isdigit():
            out[fn] = int(cyc)
    return out


def _print_rows(rows: list[tuple[str, int | None, int]], args, extra: str = "") -> None:
    """rows = (clip_name, expected_cycles | None, detected). Prints table + summary."""
    print(f"{'clip':46} {'cyc':>3} {'exp':>4} {'det':>4} {'err':>5}")
    abs_errs, errs, within1, n = [], [], 0, 0
    for name, cyc, det in rows:
        if cyc is None:
            print(f"{name[:46]:46} {'?':>3} {'?':>4} {det:>4}  (no label)")
            continue
        err = det - cyc  # near-arm recoveries == stroke_cycles (1:1)
        abs_errs.append(abs(err))
        errs.append(err)
        within1 += 1 if abs(err) <= 1 else 0
        n += 1
        print(f"{name[:46]:46} {cyc:>3} {cyc:>4} {det:>4} {err:>+5}")
    if n:
        print(
            f"\nn={n}  MAE={sum(abs_errs)/n:.2f}  bias={sum(errs)/n:+.2f}  "
            f"within±1={within1}/{n} ({100*within1/n:.0f}%)"
        )
        print(
            f"(expected = stroke_cycles, near-arm 1:1; method={args.method}; {extra})"
        )


async def _run(args) -> int:
    key = _load_key(
        Path(args.golden_root).expanduser()
        / args.category
        / f"answer_key_{args.category}.csv"
    )
    rows: list[tuple[str, int | None, int]] = []

    if args.method == "vlm":
        from services.ai_service.coach.classify import classify_strip
        from services.ai_service.coach.frames import load_frames
        from services.ai_service.pipeline.segment import group_phase_instances
        from services.ai_service.pipeline.types import Phase

        if not args.strips_root:
            print("--method vlm needs --strips-root (dir of <clip-stem>/frame_*.jpg)")
            return 1
        dirs = sorted(p for p in Path(args.strips_root).iterdir() if p.is_dir())
        total_cost = 0.0
        for d in dirs:
            frames = load_frames(d)
            if not frames:
                continue
            labels, cost = await classify_strip(
                frames, model=args.segment_model, batch=args.batch
            )
            total_cost += cost
            insts = group_phase_instances(labels, [f.timestamp_s for f in frames])
            # near-arm recoveries == stroke_cycles (1:1); fall back to all recoveries
            near = [i for i in insts if i.phase == Phase.RECOVERY and i.arm == "near"]
            recs = near or [i for i in insts if i.phase == Phase.RECOVERY]
            rows.append((d.name + ".mp4", key.get(d.name + ".mp4"), len(recs)))
        _print_rows(
            rows, args, extra=f"model={args.segment_model} cost=${total_cost:.4f}"
        )
    else:  # motion baseline
        from services.ai_service.pipeline.segment import segment_recoveries
        from services.ai_service.pipeline.track import build_track

        root = Path(args.golden_root).expanduser() / args.category
        for clip in sorted(root.glob("*.mp4")):
            track = build_track(clip, detector=args.detector)
            rows.append((clip.name, key.get(clip.name), len(segment_recoveries(track))))
        _print_rows(rows, args, extra=f"detector={args.detector}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--golden-root", default="~/Downloads/strokelab2/golden")
    ap.add_argument("--category", default="normal")
    ap.add_argument("--method", default="motion", choices=["motion", "vlm"])
    ap.add_argument("--detector", default="motion", choices=["auto", "yolo", "motion"])
    ap.add_argument(
        "--strips-root", default=None, help="vlm: dir of <clip-stem>/frame_*.jpg"
    )
    ap.add_argument(
        "--segment-model", default="gpt-4o"
    )  # gpt-5-nano returns all-indeterminate
    ap.add_argument("--batch", type=int, default=12)
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
