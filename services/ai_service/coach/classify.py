"""VLM per-frame phase classifier — the model half of Stage-1 segmentation.

The model does ONLY what it is good at: label each frame's stroke phase. It is
never asked to count or coach — counting is deterministic
(`pipeline.segment.group_phase_instances`). Built like `coach.run_gate`: reuses
`providers.base.call_vlm` (LiteLLM, retry backoff, json_object), a cheap segment
model, low image detail, temperature 0. Frames go in time-ordered batches so the
model reads them as a sequence.
"""

from __future__ import annotations

from typing import Optional

from services.ai_service.coach.frames import Frame
from services.ai_service.pipeline.segment import FrameLabel

SEGMENT_SYSTEM_PROMPT = """\
You label frames from a freestyle swim clip by stroke phase. You are shown \
several still frames IN TIME ORDER (filmed side-on, at or above the waterline). \
For EACH frame, report only what THAT single frame shows. Do NOT count, do NOT \
coach, do NOT summarise.

phase (pick exactly ONE per frame):
- "recovery": an arm is OUT OF the water, swinging forward over the surface \
(elbow/hand clearly above the water).
- "entry": the recovering hand is piercing the water in front of the head (just \
entering).
- "glide_extension": no arm is over the surface; lead arm extended forward, \
arms in/near the water.
- "breath": the head is clearly turned to the side to breathe in this frame.
- "indeterminate": cannot tell, no swimmer, only underwater visible, or blurred.
arm: which arm is recovering — "near" (closest to camera), "far", or "none".
subphase: ONLY when phase is "recovery", which part of the over-water arc this \
frame shows; otherwise "none":
- "exit": the hand has just left the water behind the hip (early recovery).
- "mid": the elbow is high and the arm is swinging forward over the surface.
- "entry": the hand is reaching forward past the head, about to enter the water.
conf: 0.0-1.0 confidence for THIS frame.

Return ONLY this JSON object: {"frames": [{"index": <the given index>, \
"phase": "...", "arm": "...", "subphase": "...", "conf": 0.0}, ...]} — exactly \
one JSON object per frame given, in order. NO counts, NO totals, NO other keys."""

_RESPONSE_FORMAT = {"type": "json_object"}
_PHASES = {"recovery", "entry", "glide_extension", "breath", "indeterminate"}
_ARMS = {"near", "far", "none"}
_SUBPHASES = {"exit", "mid", "entry", "none"}


async def classify_strip(
    frames: list[Frame],
    *,
    model: Optional[str] = None,
    image_detail: str = "low",
    batch: int = 12,
    temperature: float = 0.0,
    max_tokens: int = 2000,
) -> tuple[list[FrameLabel], float]:
    """Classify each frame's phase. Returns (labels aligned to ``frames``, cost_usd).

    Missing/unparseable frames default to ``indeterminate`` — never silently dropped.
    """
    from services.ai_service.providers.base import call_vlm

    by_index: dict[int, FrameLabel] = {}
    cost = 0.0
    for start in range(0, len(frames), batch):
        chunk = frames[start : start + batch]
        idxs = ", ".join(str(f.index) for f in chunk)
        user = (
            f"Here are {len(chunk)} still frames in time order, with indices: {idxs}. "
            "Label EACH frame; return exactly one object per frame, no totals."
        )
        try:
            resp = await call_vlm(
                system_prompt=SEGMENT_SYSTEM_PROMPT,
                user_prompt=user,
                images=[f.jpeg for f in chunk],
                model=model,
                image_detail=image_detail,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=_RESPONSE_FORMAT,
                trace_name="strokelab_segment",
            )
            cost += resp.cost_usd
            arr = resp.parse_json().get("frames", [])
        except Exception:
            arr = []
        for o in arr if isinstance(arr, list) else []:
            try:
                idx = int(o.get("index"))
            except (TypeError, ValueError):
                continue
            phase = str(o.get("phase", "indeterminate"))
            if phase not in _PHASES:
                phase = "indeterminate"
            arm = str(o.get("arm", "none"))
            if arm not in _ARMS:
                arm = "none"
            sub = str(o.get("subphase", "none"))
            if sub not in _SUBPHASES or phase != "recovery":
                sub = "none"
            subphase = "" if sub == "none" else sub
            try:
                conf = float(o.get("conf", 0.0) or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            by_index[idx] = FrameLabel(
                index=idx, phase=phase, arm=arm, subphase=subphase, conf=conf
            )

    labels = [
        by_index.get(f.index, FrameLabel(f.index, "indeterminate", "none", "", 0.0))
        for f in frames
    ]
    return labels, cost
