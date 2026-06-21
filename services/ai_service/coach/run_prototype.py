"""Run the Stroke Lab VLM coach over clips (or pre-extracted frames) and print
the coaching output plus the real token usage and cost per call.

Two ways to run, because the dev machine splits cv2 and the LLM stack across
two Pythons:

  # A) end-to-end (needs cv2 AND litellm — e.g. the ai-service container)
  python -m services.ai_service.coach.run_prototype --clips a.mp4 b.mp4

  # B) frames already extracted by frames.py (needs only litellm)
  python -m services.ai_service.coach.run_prototype --frames-root /tmp/sl_coach

Each clip's frames live in <root>/<clip_name>/frame_*.jpg.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from services.ai_service.coach.coach import CoachReport, run_coach
from services.ai_service.coach.frames import Frame, load_frames


def _frames_for_clip(clip: str, n: int) -> tuple[str, list[Frame]]:
    from services.ai_service.coach.frames import extract_key_frames  # needs cv2

    return Path(clip).stem, extract_key_frames(clip, n_frames=n)


def _print_report(name: str, rep: CoachReport) -> None:
    d = rep.raw
    print("\n" + "=" * 78)
    print(f"CLIP: {name}")
    print("-" * 78)
    if d.get("_parse_error"):
        print("  [could not parse JSON]\n" + (d.get("_raw_text") or "")[:1500])
    else:
        print(
            f"  view={d.get('view')}  stroke={d.get('stroke')}  "
            f"usable={d.get('usable_for_coaching')}  confidence={d.get('confidence')}  "
            f"swimmers={d.get('swimmer_count')}"
        )
        print(f"  summary: {d.get('summary')}")
        wk = d.get("whats_working") or []
        if wk:
            print("  what's working: " + "; ".join(wk))
        for i, fx in enumerate(d.get("priority_fixes") or [], 1):
            print(f"  fix #{i}: {fx.get('fault')}")
            print(f"           evidence: {fx.get('evidence')}")
            print(f"           why: {fx.get('why_it_matters')}")
            print(f"           drill: {fx.get('drill')}")
        hn = d.get("honest_numbers") or {}
        print(
            f"  honest numbers: ~cycles={hn.get('approx_stroke_cycles_seen')}  "
            f"breathing={hn.get('breathing_side')}"
        )
        cav = d.get("caveats") or []
        if cav:
            print("  caveats: " + "; ".join(cav))
        print(f"  handoff: {d.get('coach_handoff')}")
    print("-" * 78)
    print(
        f"  >> {rep.provider}/{rep.model} | {rep.n_frames} frames | "
        f"in={rep.input_tokens} out={rep.output_tokens} tok | "
        f"${rep.cost_usd:.5f} | {rep.latency_ms} ms"
    )


async def _amain(args: argparse.Namespace) -> int:
    jobs: list[tuple[str, list[Frame]]] = []
    if args.frames_root:
        root = Path(args.frames_root)
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            fr = load_frames(d)
            if fr:
                jobs.append((d.name, fr))
    else:
        for clip in args.clips:
            jobs.append(_frames_for_clip(clip, args.n))

    if not jobs:
        print("no clips/frames found")
        return 1

    out_dir = Path(args.out_json) if args.out_json else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    total_cost, total_in, total_out = 0.0, 0, 0
    models = [args.model] + ([args.also_model] if args.also_model else [])
    for name, frames in jobs:
        for mi, model in enumerate(models):
            # Only run the second (comparison) model on the first clip.
            if mi > 0 and name != jobs[0][0]:
                continue
            label = name if mi == 0 else f"{name}  [compare: {model}]"
            try:
                rep = await run_coach(frames, model=model, image_detail=args.detail)
            except Exception as exc:  # one bad call must not kill the batch
                print("\n" + "=" * 78)
                print(
                    f"CLIP: {label}\n  !! call failed: {type(exc).__name__}: {str(exc)[:200]}"
                )
                continue
            _print_report(label, rep)
            total_cost += rep.cost_usd
            total_in += rep.input_tokens
            total_out += rep.output_tokens
            if out_dir:
                safe = label.replace("/", "_").replace(" ", "")
                (out_dir / f"{safe}.json").write_text(
                    json.dumps(
                        {
                            "clip": name,
                            "model": rep.model,
                            "provider": rep.provider,
                            "n_frames": rep.n_frames,
                            "input_tokens": rep.input_tokens,
                            "output_tokens": rep.output_tokens,
                            "cost_usd": rep.cost_usd,
                            "latency_ms": rep.latency_ms,
                            "report": rep.raw,
                        },
                        indent=2,
                    )
                )

    print("\n" + "=" * 78)
    print(
        f"TOTAL: {total_in} in + {total_out} out tokens | ${total_cost:.5f} "
        f"across {len(jobs)} clip(s)"
    )
    if jobs:
        print(f"avg cost per clip (primary model): ${total_cost / len(jobs):.5f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--clips", nargs="+", help="video files (needs cv2)")
    src.add_argument(
        "--frames-root", help="dir of <clip>/frame_*.jpg (needs only litellm)"
    )
    ap.add_argument("--model", default=None, help="LiteLLM model string (default: env)")
    ap.add_argument(
        "--also-model", default=None, help="2nd model, run on first clip only"
    )
    ap.add_argument("--detail", default="auto", choices=["auto", "low", "high"])
    ap.add_argument("--n", type=int, default=8, help="frames per clip when extracting")
    ap.add_argument("--out-json", default=None, help="dir to dump per-clip JSON")
    return asyncio.run(_amain(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
