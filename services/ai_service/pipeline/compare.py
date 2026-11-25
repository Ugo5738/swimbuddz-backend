"""Deterministic longitudinal comparison across a swimmer's uploads ($0 VLM cost).

Implements the "compare with their next swim" coaching loop without spending
additional model tokens: extracts each run's verified aspect verdicts and
severities from stored ``coach_result`` payloads and emits a structured,
plain-English progression diff (``improved``, ``still_working``, ``regressed``,
``maintained``, ``newly_observed``).

Pure Python at import time (no cv2/litellm/SQLAlchemy dependencies).
"""

from __future__ import annotations

from typing import Any, Optional

from services.ai_service.pipeline.types import SEVERITY_FIX, SEVERITY_STRENGTH

_AREA_LABELS = {
    "body_line": "Body line",
    "head_breath": "Head & breathing",
    "body_rotation": "Body rotation",
    "recovery_elbow": "Recovery elbow",
    "entry_reach": "Entry & reach",
}

# TI coaching order for picking the headline aspect when several changed.
_TI_ORDER = (
    "body_line",
    "head_breath",
    "body_rotation",
    "entry_reach",
    "recovery_elbow",
)

# Priority of components when extracting the authoritative per-aspect summary
# from a stored PipelineResult dict.
_SOURCE_PRIORITY = {
    "aggregator": 0,
    "chunk_coach": 1,
    "body_line": 2,
    "head_breathing": 2,
    "recovery_coach": 2,
    "entry_reach": 2,
    "holistic_coach": 3,
}


def _rank_key(
    component: str, severity: str, confidence: float
) -> tuple[int, int, float]:
    comp_rank = _SOURCE_PRIORITY.get(component, 9)
    sev_rank = (
        0 if severity == SEVERITY_FIX else 1 if severity == SEVERITY_STRENGTH else 2
    )
    return (comp_rank, sev_rank, -confidence)


def extract_aspect_snapshot(
    coach_payload: Optional[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Extract a clean ``{area: {verdict, severity, confidence, observation}}``
    snapshot from a stored ``coach_result`` payload (or raw PipelineResult dict).
    """
    if not isinstance(coach_payload, dict):
        return {}
    result_dict = (
        coach_payload.get("result")
        if isinstance(coach_payload.get("result"), dict)
        else coach_payload
    )
    if not isinstance(result_dict, dict) or result_dict.get("refused"):
        return {}

    gate_tier = str(result_dict.get("gate_tier") or "clean").lower()
    if gate_tier == "refuse":
        return {}

    best: dict[str, tuple[tuple[int, int, float], dict[str, Any]]] = {}
    for cr in result_dict.get("results") or []:
        if not isinstance(cr, dict):
            continue
        comp = str(cr.get("component") or "")
        if comp in {"gate", "collate", "phase_segment", "pose_recovery"}:
            continue
        for f in cr.get("findings") or []:
            if not isinstance(f, dict) or not f.get("available", True):
                continue
            area = str(f.get("area") or "").strip()
            if area not in _AREA_LABELS:
                continue
            severity = str(f.get("severity") or "info").strip().lower()
            if severity not in {SEVERITY_FIX, SEVERITY_STRENGTH}:
                continue
            extra = f.get("extra") if isinstance(f.get("extra"), dict) else {}
            verdict = str(extra.get("verdict") or "").strip()
            conf = float(f.get("confidence", 0.0) or 0.0)
            key = _rank_key(comp, severity, conf)
            existing = best.get(area)
            if existing is None or key < existing[0]:
                best[area] = (
                    key,
                    {
                        "aspect": area,
                        "severity": severity,
                        "verdict": verdict or None,
                        "confidence": round(conf, 3),
                        "observation": str(f.get("observation") or "").strip(),
                    },
                )
    return {area: item[1] for area, item in best.items()}


def compare_runs(
    current_payload: Optional[dict[str, Any]],
    previous_payload: Optional[dict[str, Any]],
    *,
    previous_job_id: str,
    previous_completed_at: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Compare verified aspect verdicts between current and previous swim runs.

    Returns ``None`` when neither clip has comparable verified aspect readings
    (e.g., refused clip or empty findings).
    """
    cur_res = (
        current_payload.get("result")
        if isinstance(current_payload, dict)
        and isinstance(current_payload.get("result"), dict)
        else current_payload
    )
    prev_res = (
        previous_payload.get("result")
        if isinstance(previous_payload, dict)
        and isinstance(previous_payload.get("result"), dict)
        else previous_payload
    )
    if not isinstance(cur_res, dict) or not isinstance(prev_res, dict):
        return None

    cur_tier = str(cur_res.get("gate_tier") or "clean").lower()
    prev_tier = str(prev_res.get("gate_tier") or "clean").lower()

    cur_snap = extract_aspect_snapshot(current_payload)
    prev_snap = extract_aspect_snapshot(previous_payload)
    if not cur_snap or not prev_snap:
        return None

    aspects: list[dict[str, Any]] = []
    improved: list[str] = []
    still_working: list[str] = []
    regressed: list[str] = []
    maintained: list[str] = []

    ordered_areas = [a for a in _TI_ORDER if a in cur_snap or a in prev_snap]
    for area in ordered_areas:
        cur = cur_snap.get(area)
        prev = prev_snap.get(area)
        label = _AREA_LABELS[area]

        if cur and not prev:
            aspects.append(
                {
                    "aspect": area,
                    "label": label,
                    "status": "newly_observed",
                    "previous_severity": None,
                    "current_severity": cur["severity"],
                    "previous_verdict": None,
                    "current_verdict": cur["verdict"],
                    "summary": f"{label} was newly visible in this clip.",
                }
            )
            continue
        if not cur or not prev:
            continue

        p_sev, c_sev = prev["severity"], cur["severity"]
        if p_sev == SEVERITY_FIX and c_sev == SEVERITY_STRENGTH:
            status = "improved"
            improved.append(label)
            summary = f"{label} improved from a priority fix to a strength."
        elif p_sev == SEVERITY_FIX and c_sev == SEVERITY_FIX:
            status = "still_working"
            still_working.append(label)
            summary = f"{label} still shows up as an area to practise."
        elif p_sev == SEVERITY_STRENGTH and c_sev == SEVERITY_STRENGTH:
            status = "maintained"
            maintained.append(label)
            summary = f"{label} remains a consistent strength."
        else:
            status = "regressed"
            regressed.append(label)
            summary = f"{label} showed a new fault compared with your previous swim."

        aspects.append(
            {
                "aspect": area,
                "label": label,
                "status": status,
                "previous_severity": p_sev,
                "current_severity": c_sev,
                "previous_verdict": prev["verdict"],
                "current_verdict": cur["verdict"],
                "summary": summary,
            }
        )

    overlap = [a for a in aspects if a["status"] != "newly_observed"]
    if not overlap:
        return None

    parts: list[str] = []
    if improved:
        parts.append(f"Improved: {', '.join(improved)}.")
    if still_working:
        parts.append(f"Keep focusing on: {', '.join(still_working)}.")
    if not parts and maintained:
        parts.append(f"Consistent strengths: {', '.join(maintained)}.")
    if regressed and not improved:
        parts.append(f"Watch closely: {', '.join(regressed)}.")

    caveat = None
    if cur_tier == "borderline" or prev_tier == "borderline":
        caveat = (
            "One of the two clips had a marginal camera angle, so treat this "
            "comparison as a directional cue rather than a final verdict."
        )

    return {
        "previous_job_id": previous_job_id,
        "previous_completed_at": previous_completed_at,
        "comparable": True,
        "improved_count": len(improved),
        "still_working_count": len(still_working),
        "maintained_count": len(maintained),
        "regressed_count": len(regressed),
        "headline": " ".join(parts) or "Compared with your previous freestyle swim.",
        "caveat": caveat,
        "aspects": aspects,
    }
