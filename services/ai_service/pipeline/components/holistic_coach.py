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
from pathlib import Path

from libs.common.logging import get_logger
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

logger = get_logger(__name__)

_CITE = re.compile(r"(?:frame\s*#?|#)\s*(\d+)", re.I)
# Video mode cites moments by timestamp ("at t=2.1s" / "2.1s"), not frame index.
_TS = re.compile(r"(\d+(?:\.\d+)?)\s*s\b", re.I)


def _evidence_frames(text: str, frames: list) -> list[FrameRef]:
    refs: list[FrameRef] = []
    for m in _CITE.findall(text or ""):
        i = int(m)
        if 0 <= i < len(frames):  # a wrong citation is worse than none — drop it
            refs.append(FrameRef(index=i, timestamp_s=frames[i].timestamp_s))
    return refs


def _evidence_frames_video(text: str, frames: list) -> list[FrameRef]:
    """Map a video-coach timestamp citation to the nearest extracted frame, so the
    evidence-thumbnail machinery (keyed by frame index) still resolves while the
    clip player seeks to that frame's time."""
    refs: list[FrameRef] = []
    if not frames:
        return refs
    for ts in _TS.findall(text or ""):
        t = float(ts)
        i = min(range(len(frames)), key=lambda j: abs(frames[j].timestamp_s - t))
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
        # Video mode: send the clip itself to a video-capable coach (Gemini). Falls
        # back to stills if video is off, the clip is missing, or it's over the cap.
        use_video = bool(ctx.config.coach_video and ctx.video_path)
        cache = ctx.cache
        if cache is not None and "holistic" in cache:
            raw = cache["holistic"]["raw"]  # replay — no API
            model, paid = cache["holistic"].get("model", "cached"), 0.0
            used_video = bool(cache["holistic"].get("video"))
        else:
            video_bytes = self._read_clip(ctx) if use_video else None
            report = await run_coach(
                ctx.frames,
                model=ctx.config.coach_model,
                image_detail=ctx.config.coach_detail,
                stroke_hint=ctx.stroke_hint,
                gate_context=gate_context,
                goal_block=build_goal_block(ctx.coaching),  # discipline framing (§12)
                video=video_bytes,
            )
            raw, model, paid = report.raw, report.model, report.cost_usd
            used_video = video_bytes is not None
            if cache is not None:
                cache["holistic"] = {"raw": raw, "model": model, "video": used_video}
        # In video mode the coach cites timestamps; in stills mode, frame indices.
        ev = _evidence_frames_video if used_video else _evidence_frames
        conf = raw.get("confidence") or 0.0
        findings: list[Finding] = []

        for fx in raw.get("priority_fixes") or []:
            findings.append(
                Finding(
                    component=self.name,
                    observation=fx.get("fault", "") or "",
                    severity=SEVERITY_FIX,
                    evidence_frames=ev(fx.get("evidence", ""), ctx.frames),
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
                    evidence_frames=ev(w if isinstance(w, str) else "", ctx.frames),
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
                "video": used_video,
            },
        )

    def _read_clip(self, ctx: RunContext) -> bytes | None:
        """Clip bytes for inline video coaching, or None to fall back to stills
        (clip unreadable, or over the inline size cap)."""
        try:
            data = Path(ctx.video_path).read_bytes()
        except Exception as exc:
            logger.warning("video coach: could not read clip %s", exc)
            return None
        cap = ctx.config.coach_video_max_mb * 1024 * 1024
        if len(data) > cap:
            logger.info(
                "video coach: clip %.1f MB over %s MB inline cap — using stills",
                len(data) / 1024 / 1024,
                ctx.config.coach_video_max_mb,
            )
            return None
        return data
