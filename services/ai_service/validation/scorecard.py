"""Stroke Lab engine accuracy scorecard.

Runs the analysis engine over a hand-labeled golden set and reports per-metric
error vs ground truth — because the engine shipped with NO accuracy measurement
(see the project audit). Run INSIDE the ai-worker container (needs cv2/mediapipe):

    docker compose exec ai-worker-public python -m services.ai_service.validation.scorecard

Inputs: manifest.csv + clips/<filename> + labels/<clip_id>.json (see
LABELING_GUIDE.md). Outputs scorecard.json (commit as baseline) + scorecard.md,
stamped with the engine version. Per-service tests don't run in CI — this is an
opt-in manual gate.
"""

from __future__ import annotations

import asyncio
import csv
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from services.ai_service.analysis.version import STROKELAB_ENGINE_VERSION

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.csv"
LABELS_DIR = HERE / "labels"
CLIPS_DIR = HERE / "clips"


def _roll_bucket(deg: float | None) -> str | None:
    if deg is None:
        return None
    if deg < 20:
        return "flat"
    if deg < 40:
        return "moderate"
    return "strong"


def _load_manifest() -> list[dict]:
    if not MANIFEST.exists():
        return []
    with MANIFEST.open(newline="") as f:
        return [
            r
            for r in csv.DictReader(f)
            if (r.get("clip_id") or "").strip()
            and not r["clip_id"].lstrip().startswith("#")
        ]


async def _analyze(clip_path: Path):
    # Imported lazily so this module loads without cv2/mediapipe (e.g. for ruff).
    from services.ai_service.analysis.pipeline import PipelineConfig, run_analysis

    out = Path(tempfile.mkdtemp(prefix="scorecard_")) / "annotated.mp4"
    report = await run_analysis(
        clip_path, out, config=PipelineConfig(enable_summary=False)
    )
    try:
        out.unlink(missing_ok=True)
    except OSError:
        pass
    return report


def _score_usable(report, label: dict) -> dict:
    dur = report.duration_seconds or 0.0
    gt_cycles = label.get("stroke_cycles")
    stroke_ape = None
    if gt_cycles and dur > 0 and report.stroke_rate_spm:
        gt_cpm = gt_cycles / dur * 60.0
        if gt_cpm:
            stroke_ape = abs(report.stroke_rate_spm - gt_cpm) / gt_cpm

    gt_l, gt_r = label.get("breaths_left", 0), label.get("breaths_right", 0)
    pl, pr = report.breath_count_left or 0, report.breath_count_right or 0
    gt_dom = "left" if gt_l > gt_r else ("right" if gt_r > gt_l else "even")
    pr_dom = "left" if pl > pr else ("right" if pr > pl else "even")

    pred_bucket = _roll_bucket(report.body_roll_proxy_degrees)
    roll_match = (
        (pred_bucket == label.get("roll_bucket")) if label.get("roll_bucket") else None
    )
    return {
        "stroke_ape": stroke_ape,
        "breath_mae": (abs(pl - gt_l) + abs(pr - gt_r)) / 2.0,
        "dominant_side_match": gt_dom == pr_dom,
        "roll_bucket_match": roll_match,
        # We removed all "good" verdicts — a usable clip must never emit one.
        "emitted_good_verdict": any(
            o.get("severity") == "good" for o in report.observations
        ),
    }


def _degraded_honestly(report) -> bool:
    """A deliberately-bad clip should refuse to give a confident read."""
    low_conf = any(o.get("key") == "low_confidence" for o in report.observations)
    return low_conf or (report.pose_detection_rate or 0.0) < 0.5


def _mean(xs: list) -> float | None:
    vals = [x for x in xs if x is not None]
    return (sum(vals) / len(vals)) if vals else None


def _rate(flags: list) -> float | None:
    vals = [f for f in flags if f is not None]
    return (sum(1 for f in vals if f) / len(vals)) if vals else None


def _pct(x: float | None) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


async def main() -> int:
    rows = _load_manifest()
    if not rows:
        print(
            f"No clips in {MANIFEST}. Add rows + clips/<filename> + "
            f"labels/<clip_id>.json (see LABELING_GUIDE.md)."
        )
        return 1

    usable, bad, skipped = [], [], []
    for row in rows:
        cid = row["clip_id"].strip()
        clip = CLIPS_DIR / (row.get("filename") or "").strip()
        label_path = LABELS_DIR / f"{cid}.json"
        if not clip.exists() or not label_path.exists():
            skipped.append(cid)
            print(f"  skip {cid}: missing clip or label")
            continue
        label = json.loads(label_path.read_text())
        print(f"  analyzing {cid} ...")
        report = await _analyze(clip)
        if label.get("usable", True):
            usable.append((cid, _score_usable(report, label)))
        else:
            bad.append((cid, _degraded_honestly(report)))

    summary = {
        "engine_version": STROKELAB_ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_usable": len(usable),
        "n_bad": len(bad),
        "n_skipped": len(skipped),
        "stroke_mape": _mean([s["stroke_ape"] for _, s in usable]),
        "breath_mae": _mean([s["breath_mae"] for _, s in usable]),
        "dominant_side_accuracy": _rate([s["dominant_side_match"] for _, s in usable]),
        "roll_bucket_agreement": _rate([s["roll_bucket_match"] for _, s in usable]),
        "bad_clip_degradation_rate": _rate([ok for _, ok in bad]),
        "false_praise_count": sum(1 for _, s in usable if s["emitted_good_verdict"]),
        "skipped": skipped,
    }
    (HERE / "scorecard.json").write_text(json.dumps(summary, indent=2))

    md = [
        f"# Stroke Lab scorecard — engine {summary['engine_version']}",
        "",
        f"_generated {summary['generated_at']}_",
        "",
        f"- clips: {summary['n_usable']} usable, {summary['n_bad']} bad-on-purpose, "
        f"{summary['n_skipped']} skipped",
        f"- **stroke cadence MAPE:** {_pct(summary['stroke_mape'])}  "
        f"_(design gate: <10%)_",
        "- **breath count MAE:** "
        + (
            f"{summary['breath_mae']:.2f}"
            if summary["breath_mae"] is not None
            else "n/a"
        ),
        f"- **dominant breathing side accuracy:** "
        f"{_pct(summary['dominant_side_accuracy'])}",
        f"- **roll bucket agreement:** {_pct(summary['roll_bucket_agreement'])}",
        f"- **bad-clip honest-degradation rate:** "
        f"{_pct(summary['bad_clip_degradation_rate'])}  _(target: 100%)_",
        f"- **false-praise verdicts:** {summary['false_praise_count']}  _(must be 0)_",
        "",
    ]
    (HERE / "scorecard.md").write_text("\n".join(md))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
