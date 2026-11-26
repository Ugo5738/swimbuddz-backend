"""Multi-config, multi-repeat coach-labeled bake-off harness for Stroke Lab.

Evaluates candidate model/input architectures against multi-coach ground-truth
annotations before changing production defaults (ADR-013,
STROKELAB_MVP_ENGINE_EVALUATION_20261121.md).

Measures separately:
  1. Coach-to-coach inter-rater agreement (usability + leading fix area).
  2. Leading-fix agreement where coaches agree (target >= 80%).
  3. Catastrophic refusal accuracy on genuinely unusable clips (target >= 95%).
  4. Unsupported high-confidence claim rate (target < 5%) + zero ADR-013
     honesty violations (banned numbers, side-on crossover, underwater faults).
  5. Repeatability across N identical runs on the same clip (default 3x).
  6. True post-promo (Jan 2027) cost per submitted clip AND per successfully
     delivered report, plus p50 / p95 turnaround latency.

Usage::

    python -m services.ai_service.validation.bakeoff_eval \\
        --clips-root /path/to/clips \\
        --labels-dir /path/to/coach_labels \\
        --configs A_gpt4o_stills,C_gemini38_chunks_4fps_hybrid,E_gemini35_lite_chunks_4fps_hybrid \\
        --repeats 3 \\
        --out bakeoff_scorecard.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    GateTier,
    InputProfile,
    PipelineConfig,
    PipelineResult,
)
from services.ai_service.pipeline.verify import check_honesty_violations


@dataclass(frozen=True)
class CandidateSpec:
    """One architecture + model candidate to benchmark."""

    name: str
    description: str
    gate_model: str
    coach_model: str
    use_chunk_pipeline: bool
    coach_video: bool
    coach_video_fps: float = 4.0
    coach_chunk_hybrid_stills: bool = True
    adjudicator_model: Optional[str] = None
    coach_reasoning_effort: Optional[str] = "low"


CANDIDATE_PRESETS: dict[str, CandidateSpec] = {
    "A_gpt4o_stills": CandidateSpec(
        name="A_gpt4o_stills",
        description="Baseline: o4-mini gate + GPT-4o 8-frame holistic stills",
        gate_model="o4-mini",
        coach_model="gpt-4o",
        use_chunk_pipeline=False,
        coach_video=False,
        coach_chunk_hybrid_stills=False,
    ),
    "B_gemini38_chunks_4fps": CandidateSpec(
        name="B_gemini38_chunks_4fps",
        description="o4-mini gate + Gemini 3.8 Flash 4s chunks @ 4 FPS (video only)",
        gate_model="o4-mini",
        coach_model="gemini/gemini-3.8-flash",
        use_chunk_pipeline=True,
        coach_video=True,
        coach_video_fps=4.0,
        coach_chunk_hybrid_stills=False,
    ),
    "C_gemini38_chunks_4fps_hybrid": CandidateSpec(
        name="C_gemini38_chunks_4fps_hybrid",
        description="o4-mini gate + Gemini 3.8 Flash 4s chunks @ 4 FPS + high-res key stills + verifier",
        gate_model="o4-mini",
        coach_model="gemini/gemini-3.8-flash",
        use_chunk_pipeline=True,
        coach_video=True,
        coach_video_fps=4.0,
        coach_chunk_hybrid_stills=True,
    ),
    "D_gemini38_hybrid_adjudicated": CandidateSpec(
        name="D_gemini38_hybrid_adjudicated",
        description="C + GPT-5.4 mini selective high-res adjudicator on uncertain top fixes",
        gate_model="o4-mini",
        coach_model="gemini/gemini-3.8-flash",
        use_chunk_pipeline=True,
        coach_video=True,
        coach_video_fps=4.0,
        coach_chunk_hybrid_stills=True,
        adjudicator_model="gpt-5.4-mini",
    ),
    "E_gemini35_lite_chunks_4fps_hybrid": CandidateSpec(
        name="E_gemini35_lite_chunks_4fps_hybrid",
        description="Lean: Gemini 3.5 Flash-Lite gate + 4s chunks @ 4 FPS + hybrid key stills",
        gate_model="gemini/gemini-3.5-flash-lite",
        coach_model="gemini/gemini-3.5-flash-lite",
        use_chunk_pipeline=True,
        coach_video=True,
        coach_video_fps=4.0,
        coach_chunk_hybrid_stills=True,
    ),
}

# Acceptance gates from STROKELAB_MVP_ENGINE_EVALUATION_20261121.md (targets).
ACCEPTANCE_TARGETS = {
    "leading_fix_agreement_min": 0.80,
    "catastrophic_refusal_min": 0.95,
    "unsupported_high_conf_max": 0.05,
    "honesty_violation_max": 0.0,
    "repeatability_min": 0.85,
}


@dataclass(frozen=True)
class CoachAnnotation:
    """One independent coach's structured label for a single clip."""

    coach_id: str
    usable: bool
    leading_fix_area: Optional[str] = (
        None  # body_line | head_breath | body_rotation | recovery_elbow | entry_reach | none
    )
    secondary_fix_areas: tuple[str, ...] = ()
    catastrophic_unusable: bool = False
    notes: str = ""


@dataclass(frozen=True)
class ClipGroundTruth:
    """Aggregated multi-coach ground truth for one evaluation clip."""

    clip_id: str
    video_file: str
    annotations: tuple[CoachAnnotation, ...] = ()
    camera_view: str = "side-on"

    @property
    def coaches_agree_usable(self) -> bool:
        if len(self.annotations) < 2:
            return bool(self.annotations)
        return len({a.usable for a in self.annotations}) == 1

    @property
    def consensus_usable(self) -> Optional[bool]:
        if not self.annotations:
            return None
        yes = sum(1 for a in self.annotations if a.usable)
        no = len(self.annotations) - yes
        if yes == no:
            return None
        return yes > no

    @property
    def is_catastrophic_unusable(self) -> bool:
        if not self.annotations:
            return False
        votes = sum(
            1 for a in self.annotations if a.catastrophic_unusable or not a.usable
        )
        return (
            votes >= math.ceil(len(self.annotations) * 0.5)
            and self.consensus_usable is False
        )

    @property
    def agreed_leading_fix_area(self) -> Optional[str]:
        """Return the leading fix area ONLY when coaches agree on usability and top area."""
        usable_anns = [a for a in self.annotations if a.usable and a.leading_fix_area]
        if not usable_anns or self.consensus_usable is not True:
            return None
        if len(usable_anns) == 1:
            return usable_anns[0].leading_fix_area
        areas = {a.leading_fix_area for a in usable_anns}
        if len(areas) == 1:
            return next(iter(areas))
        return None

    @property
    def acceptable_fix_areas(self) -> set[str]:
        areas: set[str] = set()
        for a in self.annotations:
            if a.usable:
                if a.leading_fix_area and a.leading_fix_area != "none":
                    areas.add(a.leading_fix_area)
                areas.update(x for x in a.secondary_fix_areas if x and x != "none")
        return areas


@dataclass
class RunObservation:
    """Compact outcome of ONE pipeline execution on ONE clip."""

    clip_id: str
    repeat_idx: int
    config_name: str
    refused: bool
    gate_tier: str
    leading_fix_area: Optional[str]
    fix_areas: list[str] = field(default_factory=list)
    high_conf_fix_areas: list[str] = field(default_factory=list)
    honesty_violations: list[str] = field(default_factory=list)
    missing_evidence_fixes: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    delivered_report: bool = False


def compute_inter_rater_agreement(clips: list[ClipGroundTruth]) -> dict[str, Any]:
    """Measure coach-to-coach agreement before evaluating any AI candidate."""
    multi_coach = [c for c in clips if len(c.annotations) >= 2]
    usable_agreed = sum(1 for c in multi_coach if c.coaches_agree_usable)

    both_usable = [
        c
        for c in multi_coach
        if c.coaches_agree_usable
        and c.consensus_usable is True
        and all(a.leading_fix_area for a in c.annotations)
    ]
    fix_agreed = sum(
        1 for c in both_usable if len({a.leading_fix_area for a in c.annotations}) == 1
    )
    return {
        "clips_with_multi_coach": len(multi_coach),
        "usability_agreement_rate": (
            round(usable_agreed / len(multi_coach), 4) if multi_coach else None
        ),
        "usable_clips_with_multi_coach_fix": len(both_usable),
        "leading_fix_agreement_rate": (
            round(fix_agreed / len(both_usable), 4) if both_usable else None
        ),
    }


def extract_run_observation(
    result: PipelineResult,
    *,
    clip_id: str,
    repeat_idx: int,
    config_name: str,
    camera_view: str = "side-on",
    latency_ms: int = 0,
) -> RunObservation:
    """Project a ``PipelineResult`` into a compact ``RunObservation``."""
    # Prefer aggregator findings when present, else holistic/chunk findings.
    agg = next((r for r in result.results if r.component == "aggregator"), None)
    if agg is not None and agg.findings:
        primary_findings = agg.findings
    else:
        primary_findings = [
            f
            for r in result.results
            if r.component not in {"gate", "collate", "phase_segment", "pose_recovery"}
            for f in r.findings
        ]

    fixes = [f for f in primary_findings if f.available and f.severity == SEVERITY_FIX]
    fix_areas = [f.area for f in fixes if f.area]
    high_conf_areas = [
        f.area for f in fixes if f.area and float(f.confidence or 0.0) >= 0.70
    ]
    missing_evidence = sum(
        1 for f in fixes if result.gate_tier == GateTier.CLEAN and not f.evidence_frames
    )

    violations: list[str] = []
    for f in primary_findings:
        if not f.available:
            continue
        blob = " ".join(
            str(x)
            for x in (
                f.observation,
                (f.extra or {}).get("why_it_matters", ""),
                (f.extra or {}).get("drill", ""),
            )
            if x
        )
        violations.extend(
            check_honesty_violations(
                blob,
                profile=result.input_profile,
                view=camera_view,
                severity=f.severity,
            )
        )

    delivered = not result.refused and any(
        f.available and f.severity in {SEVERITY_FIX, "strength", "info"}
        for f in primary_findings
    )
    return RunObservation(
        clip_id=clip_id,
        repeat_idx=repeat_idx,
        config_name=config_name,
        refused=bool(result.refused),
        gate_tier=result.gate_tier.value,
        leading_fix_area=fix_areas[0] if fix_areas else None,
        fix_areas=fix_areas,
        high_conf_fix_areas=high_conf_areas,
        honesty_violations=sorted(set(violations)),
        missing_evidence_fixes=missing_evidence,
        cost_usd=round(float(result.total_cost_usd or 0.0), 6),
        latency_ms=int(latency_ms),
        delivered_report=delivered,
    )


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil((p / 100.0) * len(ordered)) - 1))
    return round(float(ordered[idx]), 4)


def score_candidate(
    config_name: str,
    ground_truth: list[ClipGroundTruth],
    observations: list[RunObservation],
) -> dict[str, Any]:
    """Score one candidate config across all clips and repeats against coach truth."""
    gt_by_id = {c.clip_id: c for c in ground_truth}
    obs_by_clip: dict[str, list[RunObservation]] = {}
    for o in observations:
        obs_by_clip.setdefault(o.clip_id, []).append(o)

    agreed_fix_denom = 0
    agreed_fix_hits = 0
    catastrophic_denom = 0
    catastrophic_refused = 0
    high_conf_claims = 0
    unsupported_high_conf = 0
    honesty_violation_runs = 0
    missing_evidence_total = 0
    repeat_denom = 0
    repeat_consistent = 0

    for clip_id, runs in obs_by_clip.items():
        gt = gt_by_id.get(clip_id)
        if gt is None:
            continue

        # Repeatability: majority agreement across >=2 identical runs on same clip.
        if len(runs) >= 2:
            repeat_denom += 1
            outcomes = [
                ("REFUSE" if r.refused else (r.leading_fix_area or "NO_FIX"))
                for r in runs
            ]
            top_count = Counter(outcomes).most_common(1)[0][1]
            if top_count / len(runs) >= 2 / 3:
                repeat_consistent += 1

        for r in runs:
            if r.honesty_violations:
                honesty_violation_runs += 1
            missing_evidence_total += r.missing_evidence_fixes

            if gt.is_catastrophic_unusable:
                catastrophic_denom += 1
                if r.refused:
                    catastrophic_refused += 1
                continue

            if gt.agreed_leading_fix_area is not None:
                agreed_fix_denom += 1
                expected = (
                    None
                    if gt.agreed_leading_fix_area == "none"
                    else gt.agreed_leading_fix_area
                )
                if not r.refused and r.leading_fix_area == expected:
                    agreed_fix_hits += 1

            acceptable = gt.acceptable_fix_areas
            for area in r.high_conf_fix_areas:
                high_conf_claims += 1
                if acceptable and area not in acceptable:
                    unsupported_high_conf += 1

    total_runs = len(observations)
    delivered_runs = sum(1 for o in observations if o.delivered_report)
    total_cost = sum(o.cost_usd for o in observations)
    latencies = [float(o.latency_ms) for o in observations]

    leading_rate = (
        round(agreed_fix_hits / agreed_fix_denom, 4) if agreed_fix_denom else None
    )
    refusal_rate = (
        round(catastrophic_refused / catastrophic_denom, 4)
        if catastrophic_denom
        else None
    )
    unsupported_rate = (
        round(unsupported_high_conf / high_conf_claims, 4) if high_conf_claims else 0.0
    )
    honesty_rate = round(honesty_violation_runs / total_runs, 4) if total_runs else 0.0
    repeat_rate = round(repeat_consistent / repeat_denom, 4) if repeat_denom else None

    gates = {
        "leading_fix_agreement": (
            leading_rate is not None
            and leading_rate >= ACCEPTANCE_TARGETS["leading_fix_agreement_min"]
        ),
        "catastrophic_refusal": (
            refusal_rate is not None
            and refusal_rate >= ACCEPTANCE_TARGETS["catastrophic_refusal_min"]
        ),
        "unsupported_high_conf": (
            unsupported_rate <= ACCEPTANCE_TARGETS["unsupported_high_conf_max"]
        ),
        "zero_honesty_violations": (
            honesty_rate <= ACCEPTANCE_TARGETS["honesty_violation_max"]
        ),
        "repeatability": (
            repeat_rate is not None
            and repeat_rate >= ACCEPTANCE_TARGETS["repeatability_min"]
        ),
    }

    return {
        "config": config_name,
        "total_runs": total_runs,
        "delivered_reports": delivered_runs,
        "leading_fix_agreement": {
            "hits": agreed_fix_hits,
            "denom": agreed_fix_denom,
            "rate": leading_rate,
        },
        "catastrophic_refusal": {
            "refused": catastrophic_refused,
            "denom": catastrophic_denom,
            "rate": refusal_rate,
        },
        "unsupported_high_conf": {
            "unsupported": unsupported_high_conf,
            "claims": high_conf_claims,
            "rate": unsupported_rate,
        },
        "honesty_violations": {
            "runs_with_violation": honesty_violation_runs,
            "rate": honesty_rate,
        },
        "missing_evidence_fixes": missing_evidence_total,
        "repeatability": {
            "consistent_clips": repeat_consistent,
            "denom": repeat_denom,
            "rate": repeat_rate,
        },
        "economics": {
            "total_cost_usd": round(total_cost, 6),
            "cost_per_submitted_usd": (
                round(total_cost / total_runs, 6) if total_runs else 0.0
            ),
            "cost_per_delivered_report_usd": (
                round(total_cost / delivered_runs, 6) if delivered_runs else None
            ),
        },
        "latency_ms": {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
        },
        "gates": gates,
        "all_gates_passed": all(gates.values()),
    }


def load_ground_truth(labels_dir: Path) -> list[ClipGroundTruth]:
    """Load multi-coach JSON label files from ``labels_dir``."""
    clips: list[ClipGroundTruth] = []
    for path in sorted(labels_dir.glob("*.json")):
        raw = json.loads(path.read_text())
        anns = tuple(
            CoachAnnotation(
                coach_id=str(a.get("coach_id") or f"coach_{i}"),
                usable=bool(a.get("usable", True)),
                leading_fix_area=a.get("leading_fix_area"),
                secondary_fix_areas=tuple(a.get("secondary_fix_areas") or ()),
                catastrophic_unusable=bool(a.get("catastrophic_unusable", False)),
                notes=str(a.get("notes") or ""),
            )
            for i, a in enumerate(raw.get("coaches") or [])
        )
        clips.append(
            ClipGroundTruth(
                clip_id=str(raw.get("clip_id") or path.stem),
                video_file=str(raw.get("video_file") or f"{path.stem}.mp4"),
                annotations=anns,
                camera_view=str(raw.get("camera_view") or "side-on"),
            )
        )
    return clips


async def _run_one_clip(
    clip_path: Path,
    gt: ClipGroundTruth,
    spec: CandidateSpec,
    repeat_idx: int,
) -> RunObservation:
    from services.ai_service.coach.frames import extract_key_frames
    from services.ai_service.pipeline.components.aggregator import AggregatorComponent
    from services.ai_service.pipeline.components.chunk_coach import ChunkCoachComponent
    from services.ai_service.pipeline.components.collate import CollateComponent
    from services.ai_service.pipeline.components.gate import GateComponent
    from services.ai_service.pipeline.components.holistic_coach import (
        HolisticCoachComponent,
    )
    from services.ai_service.pipeline.components.pose_recovery import (
        PoseRecoveryComponent,
    )
    from services.ai_service.pipeline.components.segment import PhaseSegmentComponent
    from services.ai_service.pipeline.registry import Registry
    from services.ai_service.pipeline.runner import run_pipeline
    from services.ai_service.pipeline.types import CoachContext, RunContext

    t0 = time.monotonic()
    key_frames = await asyncio.to_thread(extract_key_frames, clip_path, 8, 768)
    strip = await asyncio.to_thread(extract_key_frames, clip_path, 48, 640)

    reg = Registry()
    reg.register(GateComponent())
    if spec.use_chunk_pipeline:
        reg.register(PhaseSegmentComponent(), enabled=True)
        reg.register(PoseRecoveryComponent(), enabled=True)
        reg.register(ChunkCoachComponent(), enabled=True)
        reg.register(CollateComponent(), enabled=True)
        reg.register(AggregatorComponent(), enabled=True)
    else:
        reg.register(HolisticCoachComponent(), enabled=True)

    cfg = PipelineConfig(
        gate_model=spec.gate_model,
        coach_model=spec.coach_model,
        coach_video=spec.coach_video,
        coach_video_fps=spec.coach_video_fps,
        coach_chunk_hybrid_stills=spec.coach_chunk_hybrid_stills,
        coach_reasoning_effort=spec.coach_reasoning_effort,
        adjudicator_model=spec.adjudicator_model,
        max_coached_recoveries=3,
    )
    ctx = RunContext(
        frames=key_frames,
        strip=strip,
        profile=InputProfile.SIDE_ON_ABOVE,
        config=cfg,
        coaching=CoachContext(discipline="distance"),
        cache={},  # fresh per repeat so we measure true repeat variance!
        video_path=str(clip_path),
    )
    res = await run_pipeline(ctx, reg)
    return extract_run_observation(
        res,
        clip_id=gt.clip_id,
        repeat_idx=repeat_idx,
        config_name=spec.name,
        camera_view=gt.camera_view,
        latency_ms=int((time.monotonic() - t0) * 1000),
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--clips-root", required=True, help="Directory containing video files"
    )
    ap.add_argument(
        "--labels-dir", required=True, help="Directory of coach-labeled JSON files"
    )
    ap.add_argument(
        "--configs",
        default="A_gpt4o_stills,C_gemini38_chunks_4fps_hybrid,E_gemini35_lite_chunks_4fps_hybrid",
        help=f"Comma-separated preset names: {', '.join(CANDIDATE_PRESETS)}",
    )
    ap.add_argument("--repeats", type=int, default=3, help="Runs per clip per config")
    ap.add_argument("--out", default="bakeoff_scorecard.json")
    args = ap.parse_args()

    clips_root = Path(args.clips_root).expanduser()
    ground_truth = load_ground_truth(Path(args.labels_dir).expanduser())
    if not ground_truth:
        print("No coach label JSON files found.")
        return 1

    selected = [c.strip() for c in args.configs.split(",") if c.strip()]
    unknown = [c for c in selected if c not in CANDIDATE_PRESETS]
    if unknown:
        print(f"Unknown config preset(s): {unknown}")
        return 2

    scorecard: dict[str, Any] = {
        "clips_evaluated": len(ground_truth),
        "repeats": args.repeats,
        "inter_rater_agreement": compute_inter_rater_agreement(ground_truth),
        "acceptance_targets": ACCEPTANCE_TARGETS,
        "candidates": {},
    }

    for cfg_name in selected:
        spec = CANDIDATE_PRESETS[cfg_name]
        obs: list[RunObservation] = []
        for gt in ground_truth:
            clip_path = clips_root / gt.video_file
            if not clip_path.exists():
                continue
            for r in range(args.repeats):
                obs.append(await _run_one_clip(clip_path, gt, spec, r))
        scorecard["candidates"][cfg_name] = score_candidate(cfg_name, ground_truth, obs)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(scorecard, indent=2))
    print(json.dumps(scorecard, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
