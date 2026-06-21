"""Framework tests for the pipeline runner — no API, no cv2.

Uses fake components to verify the orchestration contract: gate tiers branch
correctly, REFUSE short-circuits, unavailable components surface honestly,
toggles work, and a failing component is isolated.

Run: PYTHONPATH=. .venv/bin/python -m pytest \
        services/ai_service/pipeline/tests/test_pipeline.py -q
"""

from __future__ import annotations

import asyncio

from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.registry import Registry
from services.ai_service.pipeline.runner import run_pipeline
from services.ai_service.pipeline.types import (
    SEVERITY_UNAVAILABLE,
    ComponentResult,
    Finding,
    GateTier,
    InputProfile,
    RunContext,
)


class FakeGate(Component):
    name = "gate"
    IS_GATE = True

    def __init__(self, tier: GateTier):
        self._tier = tier

    async def run(self, ctx):
        return ComponentResult(
            component=self.name,
            findings=[Finding(self.name, f"gate {self._tier.value}")],
            cost_usd=0.01,
            meta={"tier": self._tier, "verdict": object()},
        )


class FakeCoach(Component):
    name = "coach"

    async def run(self, ctx):
        # proves the gate verdict reached the component (coach-trusts-gate)
        assert ctx.gate is not None
        return ComponentResult(
            self.name, [Finding(self.name, "fix", severity="fix")], cost_usd=0.02
        )


class UnderwaterOnly(Component):
    name = "catch"
    profiles = (InputProfile.UNDERWATER,)

    async def run(self, ctx):  # should never run for a side-on clip
        raise AssertionError("must not run when unavailable")


class Boom(Component):
    name = "boom"

    async def run(self, ctx):
        raise RuntimeError("kaboom")


def _ctx():
    return RunContext(frames=[], profile=InputProfile.SIDE_ON_ABOVE)


def test_refuse_short_circuits():
    reg = Registry()
    reg.register(FakeGate(GateTier.REFUSE)).register(FakeCoach())
    res = asyncio.run(run_pipeline(_ctx(), reg))
    assert res.refused and res.gate_tier == GateTier.REFUSE
    assert [r.component for r in res.results] == ["gate"]  # coach never ran
    assert res.total_cost_usd == 0.01


def test_clean_runs_components():
    reg = Registry()
    reg.register(FakeGate(GateTier.CLEAN)).register(FakeCoach())
    res = asyncio.run(run_pipeline(_ctx(), reg))
    assert not res.refused
    assert {r.component for r in res.results} == {"gate", "coach"}
    assert abs(res.total_cost_usd - 0.03) < 1e-9


def test_borderline_still_coaches():
    reg = Registry()
    reg.register(FakeGate(GateTier.BORDERLINE)).register(FakeCoach())
    res = asyncio.run(run_pipeline(_ctx(), reg))
    assert not res.refused and res.meta.get("borderline") is True
    assert any(r.component == "coach" for r in res.results)


def test_unavailable_component_surfaces_honestly():
    reg = Registry()
    reg.register(FakeGate(GateTier.CLEAN)).register(UnderwaterOnly())
    res = asyncio.run(run_pipeline(_ctx(), reg))
    catch = next(r for r in res.results if r.component == "catch")
    assert catch.findings[0].severity == SEVERITY_UNAVAILABLE
    assert catch.findings[0].available is False


def test_toggle_disables_component():
    reg = Registry()
    reg.register(FakeGate(GateTier.CLEAN)).register(FakeCoach())
    reg.set_enabled("coach", False)
    res = asyncio.run(run_pipeline(_ctx(), reg))
    assert [r.component for r in res.results] == ["gate"]


def test_failing_component_is_isolated():
    reg = Registry()
    reg.register(FakeGate(GateTier.CLEAN)).register(Boom()).register(FakeCoach())
    res = asyncio.run(run_pipeline(_ctx(), reg))
    boom = next(r for r in res.results if r.component == "boom")
    assert boom.error and "kaboom" in boom.error
    assert any(
        r.component == "coach" and not r.error for r in res.results
    )  # others still ran
