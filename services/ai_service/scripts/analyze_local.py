"""
Stroke Lab — local CLI runner.

Thin wrapper around services.ai_service.analysis.run_analysis() so the
same pipeline that runs in the ARQ worker can be exercised on a local
video file. Used for the Week 1 kill-gate validation and for spot-check
debugging during future weeks.

Run from the strokelab venv (Week 1 setup):

    /tmp/strokelab-venv/bin/python services/ai_service/scripts/analyze_local.py \\
        --video /path/to/clip.mp4 \\
        --out-dir /tmp/strokelab-out

By default the LLM summary is disabled (no API key in the venv); pass
--with-summary to enable it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running as a script: ensure the project root is on sys.path so
# `services.ai_service.analysis` imports resolve when invoked via plain
# `python services/ai_service/scripts/analyze_local.py …` from the repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.ai_service.analysis import (  # noqa: E402
    DEFAULT_PIPELINE_CONFIG,
    PipelineConfig,
    run_analysis,
)


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/strokelab-out"))
    parser.add_argument(
        "--model",
        choices=["lite", "full", "heavy"],
        default=DEFAULT_PIPELINE_CONFIG.pose_model_variant,
    )
    parser.add_argument(
        "--max-side", type=int, default=DEFAULT_PIPELINE_CONFIG.max_inference_side
    )
    parser.add_argument("--no-yolo", action="store_true")
    parser.add_argument(
        "--yolo-conf",
        type=float,
        default=DEFAULT_PIPELINE_CONFIG.yolo_conf_threshold,
    )
    parser.add_argument(
        "--frame-stride",
        type=int,
        default=DEFAULT_PIPELINE_CONFIG.frame_stride,
        help="Process every N-th frame (1 = no skip)",
    )
    parser.add_argument(
        "--with-summary",
        action="store_true",
        help="Generate the LLM summary too (needs ANTHROPIC_API_KEY / etc)",
    )
    parser.add_argument("--stroke-type", default="freestyle")
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    annotated_out = args.out_dir / f"{args.video.stem}.annotated.mp4"

    config = PipelineConfig(
        pose_model_variant=args.model,
        max_inference_side=args.max_side,
        use_yolo=not args.no_yolo,
        yolo_conf_threshold=args.yolo_conf,
        frame_stride=args.frame_stride,
        enable_summary=args.with_summary,
    )
    report = await run_analysis(
        args.video,
        annotated_out,
        stroke_type=args.stroke_type,
        config=config,
    )

    report_path = args.out_dir / f"{args.video.stem}.report.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2))

    print(json.dumps(report.to_dict(), indent=2))
    print(f"\nAnnotated video: {annotated_out}", file=sys.stderr)
    print(f"Report JSON:     {report_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
