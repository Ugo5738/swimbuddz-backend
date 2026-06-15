"""Shared helpers for the Stroke Lab routers (member + public).

Keeps result-payload assembly (including drill resolution) in one place so the
member and public endpoints can't drift.
"""

from __future__ import annotations

from services.ai_service.analysis.drills import resolve_drill
from services.ai_service.models import AnalysisResult
from services.ai_service.schemas.analysis import (
    AnalysisResultPayload,
    DrillSuggestion,
    Observation,
    TrackingGap,
)


def build_result_payload(result: AnalysisResult) -> AnalysisResultPayload:
    """Map an ``AnalysisResult`` row to the client payload, resolving each
    observation's ``drill_key`` into full drill copy from the bank (the DB
    stores only the key, so swapping the bank needs no data migration)."""
    observations: list[Observation] = []
    for obs in result.observations or []:
        drill = resolve_drill(obs.get("drill_key"))
        observations.append(
            Observation(
                key=obs.get("key", ""),
                severity=obs.get("severity", "suggestion"),
                title=obs.get("title", ""),
                detail=obs.get("detail", ""),
                timestamp_s=obs.get("timestamp_s"),
                drill=DrillSuggestion(**drill) if drill else None,
            )
        )
    tracking_gaps = [
        TrackingGap(
            start_s=g.get("start_s", 0.0),
            end_s=g.get("end_s", 0.0),
            duration_s=g.get("duration_s", 0.0),
        )
        for g in (result.tracking_gaps or [])
    ]
    return AnalysisResultPayload(
        detected_stroke=result.detected_stroke,
        pose_detection_rate=result.pose_detection_rate,
        frames_total=result.frames_total,
        frames_with_pose=result.frames_with_pose,
        stroke_rate_spm=result.stroke_rate_spm,
        body_roll_proxy_degrees=result.body_roll_proxy_degrees,
        breath_count_left=result.breath_count_left,
        breath_count_right=result.breath_count_right,
        breath_balance_left_ratio=result.breath_balance_left_ratio,
        summary_text=result.summary_text,
        observations=observations,
        tracking_gaps=tracking_gaps,
    )
