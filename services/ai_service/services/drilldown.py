"""Per-instance drilldown (§12.5) — coach ONE stored instance on demand.

GATED behind ``STROKELAB_COACH_DRILLDOWN`` until segmentation count accuracy clears
~80% within ±1 (``validation/recovery_eval.py`` — currently ~53%). Below that bar
"recovery #N" mislabels the instance, so a paid inspect would cite the wrong cycle.
While locked, the inspect endpoints return **409 ``drilldown_locked``** and the UX
shows a visibly-LOCKED affordance ("unlocks at higher accuracy").

The unlock path (a focused future change — NOT built; it would spend a VLM call):
  1. rebuild a RunContext with ``cache`` seeded from ``coach_result['cache']`` so
     the gate + segment stages REPLAY at $0 (run-store-reuse);
  2. re-extract the strip frames for the requested instance from the stored clip;
  3. run the ONE requested aspect component on that instance (the only paid call);
  4. persist the new Finding into ``coach_result`` and return it (pay-per-inspect,
     billed 1 credit / micro-charge — off the per-clip budget, free on re-view).
Flip the config flag only once steps 1–4 land AND the accuracy gate passes.
"""

from __future__ import annotations

from fastapi import HTTPException

from libs.common.config import get_settings
from services.ai_service.models import AnalysisResult
from services.ai_service.pipeline.types import ASPECTS


def ensure_drilldown_unlocked() -> None:
    """409 while drilldown is gated off (the shipped state today)."""
    if not get_settings().STROKELAB_COACH_DRILLDOWN:
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


def run_inspect(result_row: AnalysisResult, aspect: str, instance_id: int) -> dict:
    """Coach one stored instance (the unlocked path). Validates the request, then
    raises 501 until the replay+coach path above is implemented."""
    if aspect not in ASPECTS:
        raise HTTPException(status_code=400, detail=f"Unknown aspect: {aspect}")
    cache = (result_row.coach_result or {}).get("cache") or {}
    instances = cache.get("instances") or []
    if not any(i.get("instance_id") == instance_id for i in instances):
        raise HTTPException(
            status_code=404, detail=f"No instance #{instance_id} to inspect"
        )
    # Gate passed but the replay+coach path isn't built yet — fail loudly rather
    # than silently, so flipping the flag early can't return a fake result.
    raise HTTPException(
        status_code=501, detail="Drilldown coaching is not yet implemented"
    )
