"""Stage-2 base — coach ONE aspect from its Stage-1 instances, goal-aware.

Shared machinery for the per-aspect analyzers (recovery_elbow, body_line,
head_breathing, entry_reach). One run():
  1. select instances of the consumed Phase (optionally arm-filtered) → reps;
  2. window the strip frames for each rep (the arc for CHUNK, the peak frame for
     FRAME);
  3. call the VLM with the aspect's system prompt + the discipline goal-block
     (``coach.rubric.build_goal_block`` — soft, honesty-fenced) — cache-aware, so a
     stored run replays for $0;
  4. parse a closed-enum verdict and route it through ``grade()`` →
     (severity, rank) for the swimmer's discipline.

Honesty stays intact: the VLM is judged on the frames; discipline only flavours
wording (the prompt block) and priority (grade). Absent instances → zero findings.
``coach_fn`` is injectable so every analyzer is unit-testable with NO API.
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable, Optional

from services.ai_service.coach.rubric import build_goal_block
from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.grade import grade
from services.ai_service.pipeline.types import (
    ComponentResult,
    Finding,
    FrameRef,
    Granularity,
    Instance,
    InputProfile,
    RunContext,
)

# coach_fn(frames, *, system_prompt, model, image_detail, max_tokens) -> (dict, cost)
CoachFn = Callable[..., Awaitable[tuple[dict, float]]]


async def _vlm_coach(
    frames,
    *,
    system_prompt: str,
    model: Optional[str] = None,
    image_detail: str = "auto",
    max_tokens: int = 400,
):
    from services.ai_service.providers.base import call_vlm  # lazy: needs litellm

    resp = await call_vlm(
        system_prompt=system_prompt,
        user_prompt="Assess ONLY what these frames clearly show and return only the JSON.",
        images=[f.jpeg for f in frames],
        model=model,
        image_detail=image_detail,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        trace_name="strokelab_aspect",
    )
    try:
        return resp.parse_json(), resp.cost_usd
    except Exception:
        return {}, resp.cost_usd


def _representatives(instances: list[Instance], k: int) -> list[Instance]:
    """Pick up to k evenly-spaced instances (k=1 → the middle one)."""
    if k >= len(instances):
        return instances
    if k == 1:
        return [instances[len(instances) // 2]]
    step = (len(instances) - 1) / (k - 1)
    return [instances[round(i * step)] for i in range(k)]


class AspectCoachComponent(Component):
    """Base for goal-aware single-aspect analyzers. Subclasses set ``name``,
    ``aspect`` (the grade/area key), ``consumes`` (Phase), ``SYSTEM_PROMPT``, and
    override ``_findings`` to map the parsed VLM response → Finding(s)."""

    aspect: str = "other"  # the grade()/AREA_LABELS key
    arm: Optional[str] = None  # "near" filters recovery to the camera-facing arm
    max_reps: int = 1  # coach a single representative by default
    image_detail: str = "auto"
    max_tokens: int = 400
    granularity = Granularity.FRAME
    profiles = (InputProfile.SIDE_ON_ABOVE, InputProfile.UNKNOWN)

    def __init__(self, coach_fn: Optional[CoachFn] = None):
        self._coach_fn = coach_fn

    # ── selection / windowing (overridable) ──────────────────────────────────
    def _instances(self, ctx: RunContext) -> list[Instance]:
        insts = [i for i in ctx.instances if i.phase == self.consumes]
        if self.arm:  # prefer the named arm; fall back to all if none of it exists
            named = [i for i in insts if i.arm == self.arm]
            insts = named or insts
        return insts

    def _rep_cap(self, ctx: RunContext) -> int:
        return self.max_reps

    def _window(self, inst: Instance, strip):
        if self.granularity == Granularity.CHUNK:
            arc = [f for f in strip if inst.start_s <= f.timestamp_s <= inst.end_s]
            if arc:
                return arc[:4]
        # FRAME (or empty arc): the single frame nearest the instance peak
        return [min(strip, key=lambda f: abs(f.timestamp_s - inst.peak_s))]

    # ── parsed VLM response → Finding(s) (subclass overrides) ────────────────
    def _findings(self, parsed: dict, inst: Instance, window, ctx) -> list[Finding]:
        verdict = str(parsed.get("verdict", "unclear"))
        conf = float(parsed.get("confidence", 0.0) or 0.0)
        return [self._mk(verdict, parsed.get("note", ""), inst, window, ctx, conf=conf)]

    def _mk(
        self,
        verdict: str,
        note: str,
        inst: Instance,
        window,
        ctx: RunContext,
        *,
        conf: float,
        area: Optional[str] = None,
        instance_id: Optional[int] = None,
        extra_payload: Optional[dict] = None,
    ) -> Finding:
        area = area or self.aspect
        severity, rank = grade(area, verdict, ctx.coaching)  # discipline re-grade ($0)
        extra = {
            "verdict": verdict,
            "rank": rank,
            "discipline": ctx.coaching.discipline,
        }
        if extra_payload:
            extra.update(extra_payload)
        return Finding(
            component=self.name,
            observation=note or f"{area}: {verdict}",
            severity=severity,
            evidence_frames=[FrameRef(f.index, f.timestamp_s) for f in window[:3]],
            confidence=conf,
            instance_id=inst.instance_id if instance_id is None else instance_id,
            area=area,
            extra=extra,
        )

    async def run(self, ctx: RunContext) -> ComponentResult:
        start = time.monotonic()
        insts = self._instances(ctx)
        if not insts:
            return ComponentResult(self.name, [])  # honest zero — nothing to coach
        strip = ctx.strip or ctx.frames
        coach_fn = self._coach_fn or _vlm_coach

        system_prompt = self.SYSTEM_PROMPT
        goal = build_goal_block(ctx.coaching)  # soft, honesty-fenced clause (or "")
        if goal:
            system_prompt = f"{system_prompt}\n\n{goal}"

        cache = ctx.cache
        findings: list[Finding] = []
        cost = 0.0
        for inst in _representatives(insts, self._rep_cap(ctx)):
            window = self._window(inst, strip)
            key = f"{self.name}:{inst.instance_id}"
            if cache is not None and key in cache:
                parsed, c = cache[key], 0.0  # replay — no API
            else:
                parsed, c = await coach_fn(
                    window,
                    system_prompt=system_prompt,
                    model=ctx.config.coach_model,
                    image_detail=self.image_detail,
                    max_tokens=self.max_tokens,
                )
                if cache is not None:
                    cache[key] = parsed
            cost += c
            findings.extend(self._findings(parsed, inst, window, ctx))

        return ComponentResult(
            self.name,
            findings,
            cost_usd=cost,
            latency_ms=int((time.monotonic() - start) * 1000),
            meta={"coached": len(findings), "available_instances": len(insts)},
        )

    async def coach_instance(
        self, ctx: RunContext, instance_id: int
    ) -> Optional[Finding]:
        """Coach ONE specific instance by id — the on-demand drilldown path. Reuses
        the same windowing/cache/grade machinery as run(); a re-inspect of an
        already-coached instance replays from the cache at $0. Returns None if the
        instance isn't present in this run."""
        inst = next(
            (
                i
                for i in ctx.instances
                if i.phase == self.consumes
                and i.instance_id == instance_id
                and (not self.arm or i.arm == self.arm)
            ),
            None,
        )
        strip = ctx.strip or ctx.frames
        if inst is None or not strip:
            return None
        window = self._window(inst, strip)
        system_prompt = self.SYSTEM_PROMPT
        goal = build_goal_block(ctx.coaching)
        if goal:
            system_prompt = f"{system_prompt}\n\n{goal}"
        cache = ctx.cache
        key = f"{self.name}:{inst.instance_id}"
        if cache is not None and key in cache:
            parsed = cache[key]  # replay — $0
        else:
            coach_fn = self._coach_fn or _vlm_coach
            parsed, _ = await coach_fn(
                window,
                system_prompt=system_prompt,
                model=ctx.config.coach_model,
                image_detail=self.image_detail,
                max_tokens=self.max_tokens,
            )
            if cache is not None:
                cache[key] = parsed
        findings = self._findings(parsed, inst, window, ctx)
        return findings[0] if findings else None
