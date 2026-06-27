"""Per-chunk multi-aspect video coach — the chunk-centric Stage-2 read.

For each of the up-to-N "free" near-arm recovery chunks, cut a short clip around
the stroke and send THAT clip (motion intact) to a video-capable VLM with a
MULTI-ASPECT prompt: assess every aspect that is CLEARLY visible in the clip —
recovery/elbow, body rotation, head/breathing, body-line — and skip what isn't.
Each visible aspect becomes its OWN graded ``Finding``, pinned to that chunk
(instance_id + peak frame), so the stroke-by-stroke lens shows a real, scrubbable
read per aspect per stroke. The collated/summary read is produced downstream by
the aggregator component (it reads ALL of these findings).

ONE VLM call per chunk (not per aspect) keeps the free-tier call count sane — the
per-aspect granularity lives in the structured response, not in N API calls. The
clip is cut to ~4s so it is far lighter (tokens) than the whole-swim video, which
is what was tripping Gemini's per-minute token cap. Falls back to the chunk's
still frames when video is off / ffmpeg is missing. ``coach_fn`` is injectable so
the whole component is unit-testable with NO API.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from libs.common.logging import get_logger
from services.ai_service.coach.rubric import build_goal_block
from services.ai_service.pipeline.components.aspect import (
    AspectCoachComponent,
    _representatives,
)
from services.ai_service.pipeline.types import (
    ComponentResult,
    Granularity,
    InputProfile,
    Instance,
    Phase,
    RunContext,
)

logger = get_logger(__name__)

# Half-window (seconds) each side of a recovery peak → a ~4s chunk clip. Wide
# enough to read rotation/head/body-line across the stroke, short enough to stay
# cheap on tokens.
CHUNK_PAD_S = 2.0

# Multi-aspect prompt. Closed-enum verdicts MUST match grade.py so grade() can
# re-grade them for the swimmer's discipline. "not visible" is a first-class
# answer — guessing an unseen aspect is the dishonesty we explicitly forbid.
CHUNK_PROMPT = """\
You are an expert freestyle swimming coach watching a SHORT VIDEO CLIP of ONE \
stroke cycle — the camera-side arm swinging forward over the water, and the body \
around it. WATCH THE MOTION across the clip.

Assess ONLY the aspects you can CLEARLY see from this angle. If an aspect is not \
clearly visible, mark it not visible — do NOT guess.

Aspects and their allowed verdicts:
- recovery_elbow: "high" (elbow leads, above the hand) | "wide" (swung out to the \
side) | "dropped" (low/trailing) | "unclear"
- body_rotation: "good" (body rolls onto each side) | "limited" (stays flat) | \
"unclear"
- head_breath: "neutral" (head still, eyes down) | "lifted" (head/eyes forward, \
sinking the hips) | "unclear"
- body_line: "flat" (hips and legs ride near the surface) | "hips_low" | \
"legs_low" | "piked" | "arched" | "unclear"

For each aspect, write ONE short, plain-English sentence describing what you saw \
(no jargon). Return ONLY this JSON:
{"aspects": [
  {"aspect": "recovery_elbow", "visible": true, "verdict": "<enum>", "note": "<sentence>", "confidence": 0.0-1.0},
  {"aspect": "body_rotation", "visible": true, "verdict": "<enum>", "note": "<sentence>", "confidence": 0.0-1.0},
  {"aspect": "head_breath", "visible": false, "verdict": "unclear", "note": "", "confidence": 0.0},
  {"aspect": "body_line", "visible": true, "verdict": "<enum>", "note": "<sentence>", "confidence": 0.0-1.0}
]}"""


def _cut_chunk(src: str, start_s: float, dur_s: float, max_mb: int) -> bytes | None:
    """Cut a [start, start+dur] window from the clip and downscale to a small 480p
    H.264 mp4 (motion kept, audio dropped) — small enough for Gemini's inline limit
    and normalised away from HEVC/.mov. Returns bytes, or None to fall back to
    stills (ffmpeg missing, transcode failed, or still over the cap)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    fd, out = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{max(0.0, start_s):.2f}",  # fast seek BEFORE -i
                "-i",
                src,
                "-t",
                f"{dur_s:.2f}",
                "-vf",
                "scale=-2:480",
                "-c:v",
                "libx264",
                "-crf",
                "30",
                "-preset",
                "veryfast",
                "-an",
                "-movflags",
                "+faststart",
                out,
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
        data = Path(out).read_bytes()
    except Exception as exc:  # ffmpeg missing/failed/timeout — degrade to stills
        logger.warning(
            "chunk coach: clip cut failed (%s) — falling back to stills", exc
        )
        return None
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass
    if len(data) > max_mb * 1024 * 1024:
        return None
    return data


class ChunkCoachComponent(AspectCoachComponent):
    """Coach the visible aspects of each free recovery chunk in one video call."""

    name = "chunk_coach"
    aspect = "chunk"  # placeholder — real area comes per-finding from the response
    consumes = Phase.RECOVERY
    arm = "near"  # camera-facing arm — most reliable side-on (fallback: far)
    granularity = Granularity.CHUNK
    image_detail = "auto"
    max_tokens = 800  # multi-aspect JSON is longer than a single-aspect verdict
    SYSTEM_PROMPT = CHUNK_PROMPT
    profiles = (InputProfile.SIDE_ON_ABOVE, InputProfile.UNKNOWN)

    def _rep_cap(self, ctx: RunContext) -> int:
        return ctx.config.max_coached_recoveries

    async def run(self, ctx: RunContext) -> ComponentResult:
        start = time.monotonic()
        insts = self._instances(ctx)
        if not insts:
            return ComponentResult(self.name, [])  # honest zero — nothing to coach
        strip = ctx.strip or ctx.frames
        reps = _representatives(insts, self._rep_cap(ctx))
        goal = build_goal_block(ctx.coaching)
        system_prompt = (
            f"{self.SYSTEM_PROMPT}\n\n{goal}" if goal else self.SYSTEM_PROMPT
        )

        cache = ctx.cache
        findings = []
        cost = 0.0
        for inst in reps:
            window = self._window(inst, strip)  # peak frames → evidence/thumbnail
            key = f"{self.name}:{inst.instance_id}"
            if cache is not None and key in cache:
                parsed = cache[key]  # replay — no API
            else:
                clip = self._read_chunk(ctx, inst) if ctx.config.coach_video else None
                parsed, c = await self._coach_chunk(window, clip, system_prompt, ctx)
                cost += c
                if cache is not None:
                    cache[key] = parsed
            findings.extend(self._findings_multi(parsed, inst, window, ctx))

        return ComponentResult(
            self.name,
            findings,
            cost_usd=cost,
            latency_ms=int((time.monotonic() - start) * 1000),
            meta={"coached_chunks": len(reps), "available_instances": len(insts)},
        )

    def _read_chunk(self, ctx: RunContext, inst: Instance) -> bytes | None:
        """The ~4s clip around this recovery, downscaled for inline video — or None
        to fall back to the chunk's stills."""
        if not ctx.video_path:
            return None
        start_s = max(0.0, inst.peak_s - CHUNK_PAD_S)
        return _cut_chunk(
            ctx.video_path, start_s, CHUNK_PAD_S * 2, ctx.config.coach_video_max_mb
        )

    async def _coach_chunk(self, window, clip, system_prompt, ctx):
        """One VLM call for this chunk → parsed multi-aspect dict + cost. Sends the
        clip as video when available (motion), else the chunk's still frames."""
        if self._coach_fn is not None:  # injected for no-API tests
            return await self._coach_fn(
                window,
                system_prompt=system_prompt,
                model=ctx.config.coach_model,
                image_detail=self.image_detail,
                max_tokens=self.max_tokens,
            )
        from services.ai_service.providers.base import call_vlm  # lazy: needs litellm

        images = [] if clip else [f.jpeg for f in window]
        resp = await call_vlm(
            system_prompt=system_prompt,
            user_prompt="Watch the clip and return only the JSON.",
            images=images,
            model=ctx.config.coach_model,
            image_detail=self.image_detail,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"},
            video=clip,
            trace_name="strokelab_chunk",
        )
        try:
            return resp.parse_json(), resp.cost_usd
        except Exception:
            return {}, resp.cost_usd

    def _findings_multi(self, parsed: dict, inst: Instance, window, ctx):
        """One graded Finding per CLEARLY-VISIBLE aspect. not-visible / unclear →
        nothing (honest silence, never a placeholder)."""
        out = []
        for a in parsed.get("aspects") or []:
            if not isinstance(a, dict) or not a.get("visible"):
                continue
            area = str(a.get("aspect") or "").strip()
            verdict = str(a.get("verdict") or "unclear").strip()
            note = str(a.get("note") or "").strip()
            if not area or verdict in ("", "unclear") or not note:
                continue
            conf = float(a.get("confidence", 0.0) or 0.0)
            out.append(
                self._mk(
                    verdict,
                    note,
                    inst,
                    window,
                    ctx,
                    conf=conf,
                    area=area,
                    extra_payload={"aspect": area, "t": round(inst.peak_s, 2)},
                )
            )
        return out
