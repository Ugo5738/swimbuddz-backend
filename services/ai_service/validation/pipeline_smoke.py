"""Cost-gated smoke test of the PRODUCTION coach pipeline (the coach-primary
worker path). Runs the same ``_run_coach_pipeline`` the worker calls — gate →
classify-every-frame → holistic + recovery — on real clips, and reports the real
$/clip + the read. This is the "does the refactor run, and what does a real clip
cost?" test; ``coach_eval.py`` separately grades coach honesty on the golden set.

Pure measurement — no DB, no queue, no credit (the worker orchestration was
adversarially reviewed). Needs cv2 + a FUNDED OpenAI key → run in the container:

    docker compose run --rm ai-worker-public \\
        python -m services.ai_service.validation.pipeline_smoke \\
            --discipline sprint --budget 1.00 clipA.mp4 clipB.mp4

``--budget`` is a HARD cap: it refuses to start a clip if the running total plus a
conservative estimate of the next would exceed it — you can't overspend. Run ONE
short clip first; clip #1 is your real cost number. SHORT clips (5-10s) are much
cheaper — the per-frame segment stage dominates. Don't repeat a clip (each run
re-pays; the $0 cache is the stored production path, not this).
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

_FIRST_GUESS = 0.15  # conservative pre-measurement gate so clip #1 can't blow it


def _summarise(res: dict) -> dict:
    findings = [f for cr in res.get("results", []) for f in cr.get("findings", [])]

    def n(sev: str) -> int:
        return sum(1 for f in findings if f.get("severity") == sev)

    top = next(
        (f.get("observation", "") for f in findings if f.get("severity") == "fix"), ""
    )
    return {
        "cost": float(res.get("total_cost_usd", 0.0)),
        "tier": "REFUSED" if res.get("refused") else str(res.get("gate_tier", "?")),
        "fix": n("fix"),
        "strength": n("strength"),
        "note": n("info"),
        "top": top,
        "components": [
            (cr.get("component", "?"), float(cr.get("cost_usd", 0.0)))
            for cr in res.get("results", [])
        ],
    }


async def _run(args) -> int:
    from services.ai_service.pipeline.types import CoachContext
    from services.ai_service.tasks.analyze import _run_coach_pipeline

    total = 0.0
    rows: list[dict] = []
    hdr = f"{'clip':26} {'tier':9} {'$cost':>8} {'fix':>3} {'str':>3} {'note':>4}  top fix"
    print(hdr)
    print("-" * len(hdr))

    for clip in args.clips:
        predicted = (total / len(rows)) if rows else _FIRST_GUESS
        if total + predicted > args.budget:
            print(
                f"\n  budget guard: next clip (~${predicted:.3f}) would exceed "
                f"${args.budget:.2f} (spent ${total:.3f}) — stopping."
            )
            break

        payload = await _run_coach_pipeline(
            Path(clip), CoachContext(discipline=args.discipline)
        )
        if payload is None:
            print("STROKELAB_ENABLE_COACH is off — set it true and re-run. aborting.")
            return 1

        s = _summarise(payload["result"])
        total += s["cost"]
        rows.append(s)
        print(
            f"{Path(clip).name[:26]:26} {s['tier']:9} ${s['cost']:>7.4f} "
            f"{s['fix']:>3} {s['strength']:>3} {s['note']:>4}  {s['top'][:42]}"
        )
        if args.verbose:
            for name, c in s["components"]:
                print(f"      · {name:20} ${c:.4f}")

    print("-" * len(hdr))
    n = len(rows)
    if not n:
        print("no clips run")
        return 0
    avg = total / n
    print(
        f"{n} clip(s) · spent ${total:.4f} · avg ${avg:.4f}/clip · "
        f"~{int(args.budget / avg) if avg else 0} clips fit in ${args.budget:.2f}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("clips", nargs="+", help="clip file path(s)")
    ap.add_argument(
        "--discipline", default="general", choices=["sprint", "distance", "general"]
    )
    ap.add_argument(
        "--budget",
        type=float,
        default=1.00,
        help="HARD $ cap — refuses to start a clip that would exceed it",
    )
    ap.add_argument(
        "--verbose", action="store_true", help="print per-component cost breakdown"
    )
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
