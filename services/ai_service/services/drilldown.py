"""Per-instance drilldown (§12.5) — coach ONE stored instance on demand.

GATED behind ``STROKELAB_COACH_DRILLDOWN`` until segmentation count accuracy clears
~80% within ±1 (``validation/recovery_eval.py`` — currently ~53%). Below that bar
"recovery #N" mislabels the instance, so a paid inspect would cite the wrong cycle.
While locked, the inspect endpoints return **409 ``drilldown_locked``** and the UX
shows a visibly-LOCKED affordance ("unlocks at higher accuracy").

The unlock path is BUILT: the inspect endpoint validates, then either returns an
already-coached instance (idempotent, $0) or enqueues ``task_inspect_instance``
(the worker re-extracts frames, replays the cache so gate/segment cost $0, coaches
the one aspect, persists the Finding). The frontend polls the job detail for it.
Billing is comped while ``STROKELAB_INSPECT_BILLING`` is off (preview mode).
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from libs.common.config import get_settings
from services.ai_service.models import AnalysisResult
from services.ai_service.pipeline.types import ASPECTS


def drilldown_unlocked() -> bool:
    """Config-driven accuracy gate: unlocked when the last-measured segmentation
    accuracy meets the bar (both knobs live in config — lower the bar to preview)."""
    s = get_settings()
    return (
        s.STROKELAB_DRILLDOWN_MEASURED_ACCURACY_PCT
        >= s.STROKELAB_DRILLDOWN_MIN_ACCURACY_PCT
    )


def ensure_drilldown_unlocked() -> None:
    """409 while drilldown is gated off (measured accuracy below the bar)."""
    if not drilldown_unlocked():
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "drilldown_locked",
                "message": (
                    "Per-stroke drilldown unlocks once our stroke detection is "
                    "accurate enough to label the exact cycle. It's coming soon."
                ),
            },
        )


def existing_inspect_finding(
    result_row: AnalysisResult, aspect: str, instance_id: int
) -> Optional[dict]:
    """The already-coached finding for this aspect+instance, if any — so a re-view
    returns instantly and never re-charges. Matches on (area, instance_id)."""
    results = ((result_row.coach_result or {}).get("result") or {}).get("results") or []
    for bucket in results:
        for f in bucket.get("findings") or []:
            if f.get("area") == aspect and f.get("instance_id") == instance_id:
                return f
    return None


def validate_inspect(result_row: AnalysisResult, aspect: str, instance_id: int) -> None:
    """400/404 if the aspect is unknown or the instance isn't in this run."""
    if aspect not in ASPECTS:
        raise HTTPException(status_code=400, detail=f"Unknown aspect: {aspect}")
    cache = (result_row.coach_result or {}).get("cache") or {}
    instances = cache.get("instances") or []
    if not any(i.get("instance_id") == instance_id for i in instances):
        raise HTTPException(
            status_code=404, detail=f"No instance #{instance_id} to inspect"
        )
