"""Run the Phase-1 pipeline over pre-extracted frames and print the result.

    python -m services.ai_service.pipeline.run --frames-dir /tmp/sl_sel/<clip> \
        [--gate-model o4-mini] [--coach-model gpt-4o] [--disable holistic_coach]

Frames are loaded via coach.frames.load_frames (so this runs without cv2; extract
first with coach.select). --disable can be repeated to toggle components off.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from services.ai_service.coach.frames import load_frames
from services.ai_service.pipeline.defaults import build_default_registry
from services.ai_service.pipeline.runner import run_pipeline
from services.ai_service.pipeline.types import InputProfile, PipelineConfig, RunContext


def _print_result(name: str, res) -> None:
    print("\n" + "=" * 74)
    print(f"CLIP: {name}")
    print(
        f"  profile={res.input_profile.value}  gate_tier={res.gate_tier.value}  "
        f"refused={res.refused}  total ${res.total_cost_usd:.4f}"
    )
    for r in res.results:
        tail = f"ERROR {r.error}" if r.error else f"${r.cost_usd:.4f} {r.latency_ms}ms"
        print(f"  [{r.component}] {tail}")
        for f in r.findings:
            ev = " ".join(f"#{e.index}" for e in f.evidence_frames)
            print(
                f"     ({f.severity}) {f.observation[:72]}"
                + (f"   [{ev}]" if ev else "")
            )
            if f.extra.get("drill"):
                print(f"        drill: {f.extra['drill'][:64]}")


async def _amain(args: argparse.Namespace) -> int:
    reg = build_default_registry()
    for name in args.disable or []:
        reg.set_enabled(name, False)

    cfg = PipelineConfig()
    if args.gate_model:
        cfg.gate_model = args.gate_model
    if args.coach_model:
        cfg.coach_model = args.coach_model
    if args.segment_model:
        cfg.segment_model = args.segment_model
    # --reuse: replay a saved run's VLM outputs from cache ($0) and re-derive.
    if args.reuse:
        from services.ai_service.pipeline.store import load_run, save_run

        cache, frames = load_run(args.reuse)
        ctx = RunContext(
            frames=frames,
            strip=frames,
            cache=cache,
            profile=InputProfile(args.profile),
            config=cfg,
        )
        res = await run_pipeline(ctx, reg)
        _print_result(Path(args.reuse).name + "  [reused]", res)
        save_run(args.reuse, clip_id=Path(args.reuse).name, ctx=ctx, result=res)
        print(
            f"\n[reuse] re-derived from cache — NEW VLM spend: ${res.total_cost_usd:.4f}"
        )
        return 0

    strip = load_frames(args.strip_dir) if args.strip_dir else []
    roots = (
        [Path(args.frames_dir)]
        if args.frames_dir
        else [p for p in sorted(Path(args.frames_root).iterdir()) if p.is_dir()]
    )
    for d in roots:
        frames = load_frames(d)
        if not frames:
            print(f"skip {d.name}: no frames")
            continue
        ctx = RunContext(
            frames=frames,
            strip=strip,
            profile=InputProfile(args.profile),
            config=cfg,
            cache={} if args.store else None,  # collect VLM outputs when storing
        )
        res = await run_pipeline(ctx, reg)
        _print_result(d.name, res)
        if args.store:
            from services.ai_service.pipeline.store import save_run

            out = Path(args.store) / d.name if args.frames_root else Path(args.store)
            save_run(out, clip_id=d.name, ctx=ctx, result=res)
            print(f"\n[store] saved run -> {out}  (paid ${res.total_cost_usd:.4f})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames-dir", help="one clip's frame dir")
    ap.add_argument("--frames-root", help="dir of <clip>/ frame dirs")
    ap.add_argument("--reuse", help="replay a saved run dir from cache ($0)")
    ap.add_argument(
        "--store", help="save the run (frames + vlm_cache + result) to this dir"
    )
    ap.add_argument("--gate-model")
    ap.add_argument("--coach-model")
    ap.add_argument("--segment-model")
    ap.add_argument(
        "--strip-dir", help="dense strip frame dir for Stage-1 segmentation"
    )
    ap.add_argument(
        "--profile", default="unknown", choices=[p.value for p in InputProfile]
    )
    ap.add_argument("--disable", action="append", help="component name to toggle off")
    args = ap.parse_args()
    if not (args.reuse or args.frames_dir or args.frames_root):
        ap.error("need one of --reuse, --frames-dir, or --frames-root")
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
