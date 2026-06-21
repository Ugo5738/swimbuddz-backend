"""Pipeline orchestration (Stage 2/3 for Phase 1).

Flow:
  1. Run the gate component → ``GateTier`` (and stash the verdict on the ctx so
     downstream components can trust it — the coach-trusts-gate contract).
  2. If REFUSE → stop (no expensive analysis; the caller shows refund + film-guide).
  3. Otherwise run every enabled, *available* analysis component. Unavailable
     components emit an honest "can't see this from this footage" finding instead
     of being skipped silently.
  4. Collate into a PipelineResult (the flat findings list is the metric surface).

One component failing never kills the run — it becomes a ComponentResult.error.
"""

from __future__ import annotations

import time

from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.registry import Registry
from services.ai_service.pipeline.types import (
    SEVERITY_UNAVAILABLE,
    ComponentResult,
    Finding,
    GateTier,
    PipelineResult,
    RunContext,
)


async def _safe_run(component: Component, ctx: RunContext) -> ComponentResult:
    start = time.monotonic()
    try:
        return await component.run(ctx)
    except Exception as exc:  # a bad component must not kill the pipeline
        return ComponentResult(
            component=component.name,
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
            latency_ms=int((time.monotonic() - start) * 1000),
        )


def _unavailable_result(component: Component) -> ComponentResult:
    return ComponentResult(
        component=component.name,
        findings=[
            Finding(
                component=component.name,
                observation=component.unavailable_reason,
                severity=SEVERITY_UNAVAILABLE,
                available=False,
                confidence=0.0,
                area=component.unavailable_area,
            )
        ],
        meta={"skipped": "unavailable"},
    )


async def run_pipeline(ctx: RunContext, registry: Registry) -> PipelineResult:
    """Run the gate + enabled analysis components over one clip's context."""
    results: list[ComponentResult] = []

    gate = registry.gate()
    if gate is None:
        raise ValueError("registry has no gate component (Component.IS_GATE=True)")

    gate_result = await _safe_run(gate, ctx)
    results.append(gate_result)
    tier: GateTier = gate_result.meta.get("tier", GateTier.BORDERLINE)
    ctx.gate = gate_result.meta.get("verdict")  # let downstream trust the gate

    total = gate_result.cost_usd
    if tier == GateTier.REFUSE:
        return PipelineResult(
            input_profile=ctx.profile,
            gate_tier=tier,
            results=results,
            total_cost_usd=total,
            refused=True,
        )

    for comp in registry.analysis_components(enabled_only=True):
        if not comp.available(ctx.profile):
            results.append(_unavailable_result(comp))
            continue
        res = await _safe_run(comp, ctx)
        total += res.cost_usd
        results.append(res)

    return PipelineResult(
        input_profile=ctx.profile,
        gate_tier=tier,
        results=results,
        total_cost_usd=total,
        meta={"borderline": tier == GateTier.BORDERLINE},
    )
