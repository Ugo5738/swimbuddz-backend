"""Holistic coach component — the validated gpt-4o coach as a pipeline component.

Wraps ``coach.run_coach`` (whole-clip, sparse-frame coaching) and converts its
JSON into unified ``Finding``s: each priority fix → a SEVERITY_FIX finding, each
genuine strength → SEVERITY_STRENGTH, with ``#N`` citations resolved to evidence
frames. This is the MVP coach *as a plug-and-play component* — Phase 2's
per-instance recovery/breath components emit the same ``Finding`` type, so the
collator and UX treat them uniformly.
"""

from __future__ import annotations

import re
import time

from services.ai_service.coach.coach import run_coach
from services.ai_service.coach.rubric import build_goal_block
from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    SEVERITY_INFO,
    SEVERITY_STRENGTH,
    ComponentResult,
    Finding,
    FrameRef,
    Granularity,
    InputProfile,
    Phase,
    RunContext,
)

_CITE = re.compile(r"(?:frame\s*#?|#)\s*(\d+)", re.I)


def _evidence_frames(text: str, frames: list) -> list[FrameRef]:
    refs: list[FrameRef] = []
    for m in _CITE.findall(text or ""):
        i = int(m)
        if 0 <= i < len(frames):  # a wrong citation is worse than none — drop it
            refs.append(FrameRef(index=i, timestamp_s=frames[i].timestamp_s))
    return refs


class HolisticCoachComponent(Component):
    name = "holistic_coach"
    consumes = Phase.CLIP
    granularity = Granularity.FRAME
    profiles = (InputProfile.SIDE_ON_ABOVE, InputProfile.UNKNOWN)

    async def run(self, ctx: RunContext) -> ComponentResult:
        start = time.monotonic()
        gate_context = None
        if ctx.gate is not None:
            gate_context = {
                "view": getattr(ctx.gate, "view", "side-on"),
                "swimmer_count": getattr(ctx.gate, "swimmer_count", 1),
            }
        cache = ctx.cache
        if cache is not None and "holistic" in cache:
            raw = cache["holistic"]["raw"]  # replay — no API
            model, paid = cache["holistic"].get("model", "cached"), 0.0
        else:
            report = await run_coach(
                ctx.frames,
                model=ctx.config.coach_model,
                image_detail=ctx.config.coach_detail,
                stroke_hint=ctx.stroke_hint,
                gate_context=gate_context,
                goal_block=build_goal_block(ctx.coaching),  # discipline framing (§12)
            )
            raw, model, paid = report.raw, report.model, report.cost_usd
            if cache is not None:
                cache["holistic"] = {"raw": raw, "model": model}
        conf = raw.get("confidence") or 0.0
        findings: list[Finding] = []

        for fx in raw.get("priority_fixes") or []:
            findings.append(
                Finding(
                    component=self.name,
                    observation=fx.get("fault", "") or "",
                    severity=SEVERITY_FIX,
                    evidence_frames=_evidence_frames(
                        fx.get("evidence", ""), ctx.frames
                    ),
                    confidence=conf,
                    area=fx.get("area"),
                    extra={
                        "why_it_matters": fx.get("why_it_matters", ""),
                        "drill": fx.get("drill", ""),
                        "evidence_text": fx.get("evidence", ""),
                    },
                )
            )
        for w in raw.get("whats_working") or []:
            findings.append(
                Finding(
                    component=self.name,
                    observation=w if isinstance(w, str) else str(w),
                    severity=SEVERITY_STRENGTH,
                    evidence_frames=_evidence_frames(
                        w if isinstance(w, str) else "", ctx.frames
                    ),
                    confidence=conf,
                )
            )
        if not findings:  # usable-but-nothing-notable, or coach hedged
            findings.append(
                Finding(
                    component=self.name,
                    observation=raw.get("summary", "No notable findings.") or "",
                    severity=SEVERITY_INFO,
                    confidence=conf,
                )
            )

        return ComponentResult(
            component=self.name,
            findings=findings,
            cost_usd=paid,  # 0 on a cache replay
            latency_ms=int((time.monotonic() - start) * 1000),
            meta={
                "summary": raw.get("summary"),
                "caveats": raw.get("caveats"),
                "coach_handoff": raw.get("coach_handoff"),
                "honest_numbers": raw.get("honest_numbers"),
                "usable_for_coaching": raw.get("usable_for_coaching"),
                "model": model,
            },
        )
