"""Shared helpers for the Stroke Lab routers (member + public).

Keeps result-payload assembly (including drill resolution) in one place so the
member and public endpoints can't drift.
"""

from __future__ import annotations

from typing import Optional

from libs.common.logging import get_logger

from services.ai_service.analysis.drills import resolve_drill
from services.ai_service.analysis.storage import signed_url_for_evidence
from services.ai_service.models import AnalysisResult
from services.ai_service.pipeline.types import ASPECTS, DISCIPLINES, LEVELS

logger = get_logger(__name__)


def parse_coach_context(
    discipline: Optional[str],
    level: Optional[str],
    focus_area: Optional[str],
    goal_text: Optional[str],
) -> dict:
    """Validate the goal-aware coaching fields from a create request into safe
    ``AnalysisJob`` kwargs (closed-enum fallbacks + ``goal_text`` clamp ≤200).
    Unknown values fall back to the conservative default rather than erroring, so
    a stale client can never block an upload."""
    gt = " ".join((goal_text or "").split())[:200] or None
    return {
        "discipline": discipline if discipline in DISCIPLINES else "general",
        "level": level if level in LEVELS else None,
        "focus_area": focus_area if focus_area in ASPECTS else None,
        "goal_text": gt,
    }


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
        # The pivot bans these — NEVER populate them (durable: the UI can't leak a
        # number we can't defend, even if a future dev re-adds a tile).
        stroke_rate_spm=None,
        body_roll_proxy_degrees=None,
        breath_count_left=None,
        breath_count_right=None,
        breath_balance_left_ratio=None,
        summary_text=result.summary_text,
        observations=observations,
        tracking_gaps=tracking_gaps,
        # Expose only the derived PipelineResult, never the internal VLM cache.
        coach_result=(result.coach_result or {}).get("result"),
        instances=_sanitized_instances(result),
    )


_INSTANCE_FIELDS = (
    "instance_id",
    "phase",
    "arm",
    "start_s",
    "end_s",
    "peak_s",
    "confidence",
)


def _sanitized_instances(result: AnalysisResult) -> Optional[list[dict]]:
    """Whitelisted projection of the stored phase instances for the per-stroke
    browser — NEVER a cache dump (the cache also holds paid VLM verdicts + the
    run-store-reuse ledger). Exposed only when drilldown is unlocked, so locked
    clients don't pay the bytes over 3G."""
    from services.ai_service.services.drilldown import drilldown_unlocked

    if not drilldown_unlocked():
        return None
    raw = ((result.coach_result or {}).get("cache") or {}).get("instances") or []
    out = [
        {k: inst.get(k) for k in _INSTANCE_FIELDS}
        for inst in raw
        if isinstance(inst, dict)
    ]
    return out or None


async def _sign_coach_image_field(
    result: AnalysisResult, field: str
) -> dict[str, str] | None:
    """Sign a coach image-key map (``evidence_keys`` / ``share_keys``) →
    {label: signed_url}. Best-effort: a signing failure drops that image (a
    missing thumbnail beats a broken one)."""
    keys = (result.coach_result or {}).get(field) or {}
    urls: dict[str, str] = {}
    for label, key in keys.items():
        try:
            urls[label] = await signed_url_for_evidence(key)
        except Exception as exc:
            logger.warning("Could not sign %s %s: %s", field, key, exc)
    return urls or None


async def sign_coach_evidence(result: AnalysisResult) -> dict[str, str] | None:
    return await _sign_coach_image_field(result, "evidence_keys")


async def sign_coach_share(result: AnalysisResult) -> dict[str, str] | None:
    return await _sign_coach_image_field(result, "share_keys")
