"""No-API tests for the Stage-2 aspect base + body_line + goal-awareness wiring.

Inject fake coach_fns so the AspectCoachComponent machinery (instance selection,
windowing, grade-routing, goal-block injection, $0 cache replay) and the body_line
analyzer are verified without any model call. Proves goal-awareness end-to-end
through real components: the SAME verdict re-grades by discipline, and the soft
goal-block reaches the prompt only when it should.
"""

from __future__ import annotations

import asyncio

from services.ai_service.coach.frames import Frame
from services.ai_service.pipeline.components.body_line import BodyLineComponent
from services.ai_service.pipeline.components.entry_reach import EntryReachComponent
from services.ai_service.pipeline.components.head_breathing import (
    HeadBreathingComponent,
)
from services.ai_service.pipeline.components.recovery_coach import (
    RecoveryCoachComponent,
)
from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    SEVERITY_INFO,
    SEVERITY_STRENGTH,
    CoachContext,
    Instance,
    Phase,
    PipelineConfig,
    RunContext,
)


def _strip(n, dt=0.3):
    return [Frame(index=i, timestamp_s=round(i * dt, 3), jpeg=b"x") for i in range(n)]


def _glide_ctx(discipline="general", cache=None):
    strip = _strip(6)
    ctx = RunContext(
        frames=strip,
        strip=strip,
        coaching=CoachContext(discipline=discipline),
        cache=cache,
    )
    ctx.instances = [Instance(Phase.GLIDE, 0, 0.6, 1.2, 0.9)]
    return ctx


async def _flat(frames, **kw):
    return {"body_line": "flat", "note": "level body line", "confidence": 0.6}, 0.0


# ── body_line analyzer ────────────────────────────────────────────────────────
def test_body_line_emits_graded_finding():
    async def fake(frames, **kw):
        return {"body_line": "hips_low", "note": "hips sit low", "confidence": 0.7}, 0.0

    res = asyncio.run(BodyLineComponent(coach_fn=fake).run(_glide_ctx()))
    assert len(res.findings) == 1
    f = res.findings[0]
    assert f.area == "body_line"
    assert f.severity == SEVERITY_FIX  # a sinking hip is a fault for everyone
    assert f.extra["verdict"] == "hips_low"
    assert f.evidence_frames  # cites the glide frame


def test_body_line_honest_zero_without_a_glide_instance():
    ctx = RunContext(frames=_strip(4), strip=_strip(4))
    ctx.instances = [Instance(Phase.RECOVERY, 0, 0.3, 0.6, 0.45, arm="near")]

    async def fake(frames, **kw):
        raise AssertionError("must not coach with no glide instance")

    res = asyncio.run(BodyLineComponent(coach_fn=fake).run(ctx))
    assert res.findings == []


# ── goal-awareness through a real component ───────────────────────────────────
def test_discipline_reranks_same_verdict_without_changing_severity():
    async def fake(frames, **kw):
        return {"body_line": "hips_low", "note": "low", "confidence": 0.6}, 0.0

    def first(disc):
        return asyncio.run(
            BodyLineComponent(coach_fn=fake).run(_glide_ctx(disc))
        ).findings[0]

    sprint, distance = first("sprint"), first("distance")
    assert sprint.severity == distance.severity == SEVERITY_FIX  # honesty: unchanged
    assert distance.extra["rank"] < sprint.extra["rank"]  # distance ranks sink higher


def test_recovery_wide_is_info_for_sprint_but_fix_for_distance():
    # the promoted recovery_coach now routes elbow → grade(): a wide recovery is a
    # fix for a distance swimmer but only an observation for a sprinter.
    async def fake(frames, **kw):
        return {
            "assessment": "arm swings wide",
            "elbow": "wide",
            "confidence": 0.7,
        }, 0.0

    def sev(disc):
        ctx = RunContext(
            frames=_strip(6),
            strip=_strip(6),
            coaching=CoachContext(discipline=disc),
        )
        ctx.instances = [Instance(Phase.RECOVERY, 0, 0.3, 0.9, 0.6, arm="near")]
        return (
            asyncio.run(RecoveryCoachComponent(coach_fn=fake).run(ctx))
            .findings[0]
            .severity
        )

    assert sev("sprint") == SEVERITY_INFO
    assert sev("distance") == SEVERITY_FIX


def test_goal_block_reaches_prompt_for_sprint_not_for_general():
    seen: dict[str, str] = {}

    async def capture(frames, *, system_prompt, **kw):
        seen["prompt"] = system_prompt
        return {"body_line": "flat", "note": "ok", "confidence": 0.6}, 0.0

    asyncio.run(BodyLineComponent(coach_fn=capture).run(_glide_ctx("sprint")))
    sprint_prompt = seen["prompt"]
    asyncio.run(BodyLineComponent(coach_fn=capture).run(_glide_ctx("general")))
    general_prompt = seen["prompt"]

    assert "SWIMMER GOAL" in sprint_prompt and "dead spot" in sprint_prompt.lower()
    assert "SWIMMER GOAL" not in general_prompt  # general default adds nothing


# ── cache replay ──────────────────────────────────────────────────────────────
def test_aspect_replays_from_cache_without_calling_vlm():
    cache: dict = {}
    asyncio.run(BodyLineComponent(coach_fn=_flat).run(_glide_ctx(cache=cache)))
    assert "body_line:0" in cache  # paid output cached

    async def boom(frames, **kw):
        raise AssertionError("must not call the VLM on a cache replay")

    res = asyncio.run(BodyLineComponent(coach_fn=boom).run(_glide_ctx(cache=cache)))
    assert res.cost_usd == 0.0
    assert res.findings and res.findings[0].extra["verdict"] == "flat"


# ── entry_reach: crossover ban + the dead-spot flip ───────────────────────────
def _entry_ctx(discipline="general"):
    strip = _strip(6)
    ctx = RunContext(
        frames=strip, strip=strip, coaching=CoachContext(discipline=discipline)
    )
    ctx.instances = [Instance(Phase.ENTRY, 0, 0.9, 0.9, 0.9)]
    return ctx


def test_entry_long_reach_flips_distance_strength_vs_sprint_hedged_info():
    async def fake(frames, **kw):
        return {"entry": "clean_extended", "note": "", "confidence": 0.8}, 0.0

    distance = asyncio.run(
        EntryReachComponent(coach_fn=fake).run(_entry_ctx("distance"))
    ).findings[0]
    sprint = asyncio.run(
        EntryReachComponent(coach_fn=fake).run(_entry_ctx("sprint"))
    ).findings[0]
    assert distance.severity == SEVERITY_STRENGTH  # free distance per stroke
    assert sprint.severity == SEVERITY_INFO  # only a hedged dead-spot caution
    assert sprint.confidence <= 0.5  # hedged — a pause lives between frames
    assert "dead-spot" in sprint.observation.lower()


def test_entry_overreach_is_info_never_a_crossover_fix():
    async def fake(frames, **kw):
        return {"entry": "overreach", "note": "reaching past", "confidence": 0.6}, 0.0

    for disc in ("sprint", "distance", "general"):
        f = asyncio.run(
            EntryReachComponent(coach_fn=fake).run(_entry_ctx(disc))
        ).findings[0]
        assert f.severity == SEVERITY_INFO
        assert "crossover" not in f.observation.lower()


# ── head_breathing: two findings, breath side only on a breath frame ──────────
def test_head_breathing_emits_head_and_breath_side_on_a_breath_frame():
    strip = _strip(6)
    ctx = RunContext(frames=strip, strip=strip)
    ctx.instances = [Instance(Phase.BREATH, 0, 1.2, 1.2, 1.2)]

    async def fake(frames, **kw):
        return {
            "head": "lifted",
            "breath_side": "right",
            "note": "head lifts to breathe",
            "confidence": 0.7,
        }, 0.0

    findings = asyncio.run(HeadBreathingComponent(coach_fn=fake).run(ctx)).findings
    kinds = {f.extra.get("kind") for f in findings}
    assert kinds == {"head", "breath_side"}
    head = next(f for f in findings if f.extra["kind"] == "head")
    side = next(f for f in findings if f.extra["kind"] == "breath_side")
    assert head.severity == SEVERITY_FIX  # a lifted head is a fault for everyone
    assert side.severity == SEVERITY_INFO  # breath side is never a fault
    assert all(f.area == "head_breath" for f in findings)


def test_head_breathing_glide_fallback_reports_head_only_no_breath_side():
    strip = _strip(6)
    ctx = RunContext(frames=strip, strip=strip)
    ctx.instances = [Instance(Phase.GLIDE, 0, 0.6, 1.2, 0.9)]  # no breath in the clip

    async def fake(frames, **kw):
        # even if the model volunteers a side, a glide frame must not report one
        return {"head": "neutral", "breath_side": "left", "confidence": 0.6}, 0.0

    findings = asyncio.run(HeadBreathingComponent(coach_fn=fake).run(ctx)).findings
    assert [f.extra["kind"] for f in findings] == ["head"]  # head only
    assert findings[0].severity == SEVERITY_STRENGTH  # neutral head is good


# ── recovery consistency / fatigue (aggregate across instances) ───────────────
def _recovery_ctx(n):
    strip = _strip(2 * n + 2)
    ctx = RunContext(
        frames=strip, strip=strip, config=PipelineConfig(max_coached_recoveries=n)
    )
    ctx.instances = [
        Instance(Phase.RECOVERY, i, i * 0.6, i * 0.6 + 0.3, i * 0.6 + 0.15, arm="near")
        for i in range(n)
    ]
    return ctx


def test_consistency_flags_fatigue_when_late_recoveries_drop():
    elbows = iter(["high", "high", "dropped"])  # clean early, drops late

    async def fake(frames, **kw):
        return {"assessment": "r", "elbow": next(elbows), "confidence": 0.8}, 0.0

    res = asyncio.run(RecoveryCoachComponent(coach_fn=fake).run(_recovery_ctx(3)))
    agg = [f for f in res.findings if f.area == "consistency"]
    assert len(agg) == 1
    assert agg[0].severity == SEVERITY_FIX and agg[0].extra["trend"] == "declining"


def test_consistency_silent_with_one_recovery():
    async def fake(frames, **kw):
        return {"assessment": "r", "elbow": "high", "confidence": 0.8}, 0.0

    res = asyncio.run(RecoveryCoachComponent(coach_fn=fake).run(_recovery_ctx(1)))
    # only the single per-instance read — no aggregate (can't compare one stroke)
    assert not any(f.area == "consistency" for f in res.findings)


# ── coach_instance: the on-demand drilldown core ──────────────────────────────
def test_coach_instance_targets_the_requested_recovery():
    async def fake(frames, **kw):
        return {"assessment": "drops here", "elbow": "dropped", "confidence": 0.7}, 0.0

    f = asyncio.run(
        RecoveryCoachComponent(coach_fn=fake).coach_instance(_recovery_ctx(3), 1)
    )
    assert f is not None
    assert f.instance_id == 1 and f.area == "recovery_elbow"
    assert f.extra["elbow"] == "dropped"


def test_coach_instance_returns_none_for_a_missing_instance():
    async def fake(frames, **kw):
        raise AssertionError("must not coach a non-existent instance")

    f = asyncio.run(
        RecoveryCoachComponent(coach_fn=fake).coach_instance(_recovery_ctx(2), 99)
    )
    assert f is None


def test_coach_instance_replays_from_cache_at_zero_cost():
    ctx = _recovery_ctx(3)
    ctx.cache = {
        "recovery_coach:1": {"assessment": "c", "elbow": "high", "confidence": 0.6}
    }

    async def boom(frames, **kw):
        raise AssertionError("must not call the VLM on a cache replay")

    f = asyncio.run(RecoveryCoachComponent(coach_fn=boom).coach_instance(ctx, 1))
    assert f is not None and f.extra["elbow"] == "high"  # served from the cache
