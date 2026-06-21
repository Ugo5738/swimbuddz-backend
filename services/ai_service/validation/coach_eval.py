"""Golden-set eval for the VLM coach (gate accuracy + coach honesty + cost).

Complements ``scorecard.py`` (which graded the deprecated metrics engine). This
grades the new gate→coach pipeline against the hand-labeled golden set:

  * GATE accuracy — accept the side-on freestyle clips (normal/, drills/),
    refuse the non-side-on ones (degraded/).
  * COACH honesty — per coach model, count violations that are mechanically
    checkable: banned numbers (SPM/cadence/degrees), faults on aspects a
    side-on above-water clip can't show (catch/underwater pull/kick),
    crossover claims (a top-down fault, risky from the side), and broken/absent
    frame citations. Plus coverage + cost.

Quality (is the advice *right*?) is not mechanically checkable — an adversarial
judge panel handles that separately; this harness produces the per-clip coach
outputs it scores.

Reads PRE-EXTRACTED frames so it runs without cv2:
    <frames-root>/<category>/<clip>/frame_*.jpg     (category ∈ normal|drills|degraded)

    python -m services.ai_service.validation.coach_eval \
        --frames-root /tmp/sl_eval --coach-models gpt-4o,gpt-5-mini \
        --gate-model gpt-5-nano --gate-votes 3 --out coach_scorecard.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from services.ai_service.coach.coach import run_coach, run_gate
from services.ai_service.coach.frames import load_frames

# Categories whose clips SHOULD pass the gate vs SHOULD be refused.
ACCEPT_CATS = {"normal", "drills"}
REFUSE_CATS = {"degraded"}

_BANNED = re.compile(
    r"(strokes?\s*per\s*minute|\bspm\b|\bcadence\b|\btempo\b|\d+\s*°|\bdegrees?\b)",
    re.I,
)
# Aspects a side-on, above-water phone clip structurally cannot show.
_INVISIBLE = re.compile(r"\b(catch|underwater\s+pull|pull[- ]?through|kick)\b", re.I)
_CROSSOVER = re.compile(r"cross[- ]?over", re.I)
_CITE = re.compile(r"(?:frame\s*#?|#)\s*(\d+)", re.I)


def _check_coach(raw: dict, n_frames: int) -> dict:
    fixes = raw.get("priority_fixes") or []
    banned = invisible = crossover = badcite = 0
    for fx in fixes:
        fault = fx.get("fault", "") or ""
        blob = f"{fault} {fx.get('why_it_matters', '') or ''}"
        if _BANNED.search(blob):
            banned += 1
        if _INVISIBLE.search(fault):
            invisible += 1
        if _CROSSOVER.search(fault):
            crossover += 1
        cites = _CITE.findall(fx.get("evidence", "") or "")
        if not cites or any(int(c) >= n_frames for c in cites):
            badcite += 1
    if _BANNED.search(raw.get("summary", "") or ""):
        banned += 1
    return {
        "n_fixes": len(fixes),
        "banned_numbers": banned,
        "invisible_aspect_faults": invisible,
        "crossover_faults": crossover,
        "bad_citations": badcite,
        "confidence": raw.get("confidence"),
        "usable": raw.get("usable_for_coaching"),
    }


async def _eval_clip(cat, clip_dir, gate_model, gate_votes, coach_models, sem) -> dict:
    frames = load_frames(clip_dir)
    n = len(frames)
    async with sem:
        gate = await run_gate(
            frames, model=gate_model, n_votes=gate_votes, image_detail="low"
        )
    row = {
        "clip": clip_dir.name,
        "category": cat,
        "n_frames": n,
        "gate_usable": gate.usable,
        "gate_view": gate.view,
        "gate_agreement": gate.agreement,
        "gate_cost": gate.cost_usd,
        "coach": {},
    }
    if gate.usable:
        gc = {"view": gate.view, "swimmer_count": gate.swimmer_count}
        for m in coach_models:
            async with sem:
                try:
                    c = await run_coach(
                        frames, model=m, gate_context=gc, max_tokens=3000
                    )
                    row["coach"][m] = {
                        **_check_coach(c.raw, n),
                        "cost": c.cost_usd,
                        "fixes": c.raw.get("priority_fixes") or [],
                    }
                except Exception as e:
                    row["coach"][m] = {"error": f"{type(e).__name__}: {str(e)[:90]}"}
    return row


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames-root", required=True)
    ap.add_argument("--coach-models", default="gpt-4o,gpt-5-mini")
    ap.add_argument("--gate-model", default="gpt-5-nano")
    ap.add_argument("--gate-votes", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default="coach_scorecard.json")
    args = ap.parse_args()

    coach_models = [m.strip() for m in args.coach_models.split(",") if m.strip()]
    root = Path(args.frames_root)
    jobs = []
    for cat_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for clip_dir in sorted(p for p in cat_dir.iterdir() if p.is_dir()):
            jobs.append((cat_dir.name, clip_dir))
    if not jobs:
        print(f"no clip dirs under {root}/<category>/<clip>/")
        return 1

    sem = asyncio.Semaphore(args.concurrency)
    rows = await asyncio.gather(
        *(
            _eval_clip(c, d, args.gate_model, args.gate_votes, coach_models, sem)
            for c, d in jobs
        )
    )

    # ── gate accuracy ──
    gate = {}
    for r in rows:
        g = gate.setdefault(r["category"], {"n": 0, "accepted": 0, "cost": 0.0})
        g["n"] += 1
        g["accepted"] += 1 if r["gate_usable"] else 0
        g["cost"] += r["gate_cost"]

    # ── coach honesty/coverage (over gate-accepted clips) ──
    coach = {
        m: {
            "n": 0,
            "fixes": 0,
            "banned_numbers": 0,
            "invisible_aspect_faults": 0,
            "crossover_faults": 0,
            "bad_citations": 0,
            "covered": 0,
            "errors": 0,
            "cost": 0.0,
        }
        for m in coach_models
    }
    for r in rows:
        for m, c in r["coach"].items():
            agg = coach[m]
            if "error" in c:
                agg["errors"] += 1
                continue
            agg["n"] += 1
            agg["fixes"] += c["n_fixes"]
            for k in (
                "banned_numbers",
                "invisible_aspect_faults",
                "crossover_faults",
                "bad_citations",
            ):
                agg[k] += c[k]
            agg["covered"] += 1 if c["n_fixes"] > 0 else 0
            agg["cost"] += c.get("cost", 0.0)

    summary = {"gate": gate, "coach": coach}
    Path(args.out).write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2, default=str)
    )

    print("\n===== GATE ACCURACY =====")
    for cat, g in sorted(gate.items()):
        want = "ACCEPT" if cat in ACCEPT_CATS else "REFUSE"
        correct = g["accepted"] if cat in ACCEPT_CATS else g["n"] - g["accepted"]
        print(
            f"  {cat:9} (want {want}): {g['accepted']}/{g['n']} accepted -> "
            f"{correct}/{g['n']} correct   gate ${g['cost']:.4f}"
        )
    print("\n===== COACH (over gate-accepted clips; lower violations = better) =====")
    for m, a in coach.items():
        n = a["n"] or 1
        print(f"  {m}:")
        print(
            f"     coached {a['n']} (errors {a['errors']}) | coverage {a['covered']}/{a['n']} | "
            f"avg fixes {a['fixes']/n:.2f} | ${a['cost']:.4f} (${a['cost']/n:.4f}/clip)"
        )
        print(
            f"     VIOLATIONS: banned#={a['banned_numbers']} invisible-aspect={a['invisible_aspect_faults']} "
            f"crossover={a['crossover_faults']} bad-cite={a['bad_citations']}"
        )
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
