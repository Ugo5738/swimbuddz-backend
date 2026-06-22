"""No-API tests for the Stage-1 segment, Stage-3 collate, and per-instance coach.

Inject fake classify/coach functions so the component wiring (set ctx.instances
for ALL phases, no counting in segment, hedged count in collate, tag instance_id,
honest-zero when no recoveries, $0 cache replay) is verified without any model call.
"""

from __future__ import annotations

import asyncio

from services.ai_service.coach.frames import Frame
from services.ai_service.coach.pose import RecoveryResult
from services.ai_service.pipeline.components.collate import CollateComponent
from services.ai_service.pipeline.components.pose_count import PoseCountComponent
from services.ai_service.pipeline.components.recovery_coach import (
    RecoveryCoachComponent,
)
from services.ai_service.pipeline.components.segment import PhaseSegmentComponent
from services.ai_service.pipeline.segment import FrameLabel
from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    Instance,
    Phase,
    RunContext,
)

# two recovery groups with a 2-frame glide gap (1-frame gaps are absorbed by the
# median-smoother — real glides span several frames).
_PHASES = [
    "glide_extension",
    "glide_extension",
    "recovery",
    "recovery",
    "glide_extension",
    "glide_extension",
    "recovery",
    "recovery",
    "glide_extension",
    "glide_extension",
]
_ARMS = ["none", "none", "near", "near", "none", "none", "near", "near", "none", "none"]


def _strip(n, dt=0.3):
    return [Frame(index=i, timestamp_s=round(i * dt, 3), jpeg=b"x") for i in range(n)]


def _recs(instances):
    return [i for i in instances if i.phase == Phase.RECOVERY]


def test_segment_sets_all_phase_instances_and_does_not_count():
    strip = _strip(10)

    async def fake_classify(s, **kw):
        return [
            FrameLabel(f.index, _PHASES[i], _ARMS[i], conf=0.9) for i, f in enumerate(s)
        ], 0.0

    ctx = RunContext(frames=strip, strip=strip, cache={})
    res = asyncio.run(PhaseSegmentComponent(classify_fn=fake_classify).run(ctx))
    # segment ONLY segments: no findings, no counting here
    assert res.findings == []
    assert len(_recs(ctx.instances)) == 2  # two near-arm recovery groups
    assert res.meta["by_phase"]["recovery"] == 2
    assert "glide" in res.meta["by_phase"]  # other phases kept, not discarded
    # full instances persisted for the worker → swim_frame_labels / coach_result
    assert ctx.cache["labels"] and ctx.cache["instances"]


def test_collate_emits_hedged_near_arm_count():
    ctx = RunContext(frames=_strip(2), strip=_strip(2))
    ctx.instances = [
        Instance(Phase.RECOVERY, 0, 0.3, 0.6, 0.45, arm="near"),
        Instance(Phase.RECOVERY, 1, 1.2, 1.5, 1.35, arm="near"),
        Instance(Phase.RECOVERY, 0, 0.9, 1.1, 1.0, arm="far"),
        Instance(Phase.GLIDE, 0, 0.0, 0.2, 0.1),
    ]
    res = asyncio.run(CollateComponent().run(ctx))
    f = res.findings[0]
    assert f.extra["recovery_count_hedged"] == 2  # near-arm == stroke_cycles
    assert f.extra["near_arm_recoveries"] == 2
    assert f.extra["far_arm_recoveries"] == 1  # far kept, just not the count
    assert "~2" in f.observation  # hedged, not a hard number


def test_recovery_coach_tags_instance_and_flags_fault():
    ctx = RunContext(frames=_strip(5), strip=_strip(5))
    ctx.instances = [
        Instance(Phase.RECOVERY, 0, 0.3, 0.9, 0.6, arm="near"),
    ]

    async def fake_coach(frames, **kw):
        return {
            "assessment": "elbow drops on recovery",
            "elbow": "dropped",
            "confidence": 0.8,
        }, 0.0

    res = asyncio.run(RecoveryCoachComponent(coach_fn=fake_coach).run(ctx))
    assert len(res.findings) == 1
    assert res.findings[0].instance_id == 0
    assert res.findings[0].severity == SEVERITY_FIX  # dropped elbow → a fix
    assert res.findings[0].evidence_frames  # carries the evidence window


def test_segment_replays_from_cache_without_calling_vlm():
    strip = _strip(10)

    async def fake_classify(s, **kw):
        return [
            FrameLabel(f.index, _PHASES[i], _ARMS[i], conf=0.9) for i, f in enumerate(s)
        ], 0.05

    cache: dict = {}
    asyncio.run(
        PhaseSegmentComponent(classify_fn=fake_classify).run(
            RunContext(frames=strip, strip=strip, cache=cache)
        )
    )
    assert "labels" in cache  # the paid output got cached

    async def boom(s, **kw):
        raise AssertionError("classify must NOT be called on a cache replay")

    ctx2 = RunContext(frames=strip, strip=strip, cache=cache)
    res = asyncio.run(PhaseSegmentComponent(classify_fn=boom).run(ctx2))
    assert res.cost_usd == 0.0  # $0 on replay
    assert len(_recs(ctx2.instances)) == 2  # re-derived from cached labels


def test_recovery_coach_honest_zero_when_no_recoveries():
    ctx = RunContext(frames=_strip(5), strip=_strip(5))
    ctx.instances = []

    async def fake_coach(frames, **kw):  # should never be called
        raise AssertionError("must not coach with no instances")

    res = asyncio.run(RecoveryCoachComponent(coach_fn=fake_coach).run(ctx))
    assert res.findings == []


def test_pose_count_sets_ctx_and_replays_from_cache():
    async def fake_count(ctx):
        return RecoveryResult(
            count=12, confidence="ok", detection_rate=0.96, near_wrist_conf=0.5
        )

    cache: dict = {}
    ctx = RunContext(frames=_strip(3), strip=_strip(3), cache=cache)
    res = asyncio.run(PoseCountComponent(count_fn=fake_count).run(ctx))
    assert res.findings == []  # pose_count emits no user-facing finding
    assert ctx.pose_recovery["count"] == 12
    assert ctx.pose_recovery["refused"] is False
    assert cache["pose_recovery"]["count"] == 12  # cached for $0 replay

    async def boom(ctx):
        raise AssertionError("pose count must NOT run on a cache replay")

    ctx2 = RunContext(frames=_strip(3), strip=_strip(3), cache=cache)
    res2 = asyncio.run(PoseCountComponent(count_fn=boom).run(ctx2))
    assert res2.meta.get("replayed") is True
    assert ctx2.pose_recovery["count"] == 12


def test_pose_count_no_clip_marks_unavailable():
    async def fake_count(ctx):
        return None  # no video_path / no frames

    ctx = RunContext(frames=_strip(2), strip=_strip(2), cache={})
    res = asyncio.run(PoseCountComponent(count_fn=fake_count).run(ctx))
    assert ctx.pose_recovery is None
    assert res.meta.get("available") is False


def test_collate_prefers_pose_count_over_vlm():
    ctx = RunContext(frames=_strip(2), strip=_strip(2))
    ctx.instances = [Instance(Phase.RECOVERY, 0, 0.3, 0.6, 0.45, arm="near")]  # VLM=1
    ctx.pose_recovery = {
        "count": 12,
        "confidence": "ok",
        "detection_rate": 0.96,
        "near_wrist_conf": 0.5,
        "refused": False,
    }
    res = asyncio.run(CollateComponent().run(ctx))
    f = res.findings[0]
    assert f.extra["recovery_count_hedged"] == 12  # the pose count, not the VLM's 1
    assert f.extra["count_source"] == "pose"
    assert f.confidence == 0.8
    assert "~12" in f.observation


def test_collate_suppresses_count_when_pose_refused():
    ctx = RunContext(frames=_strip(2), strip=_strip(2))
    ctx.instances = [Instance(Phase.RECOVERY, 0, 0.3, 0.6, 0.45, arm="near")]
    ctx.pose_recovery = {
        "count": None,
        "confidence": "low_detection",
        "detection_rate": 0.25,
        "near_wrist_conf": 0.27,
        "refused": True,
    }
    res = asyncio.run(CollateComponent().run(ctx))
    f = res.findings[0]
    # None ⇒ frontend keys on a numeric count, so the count card + drilldown hide
    assert f.extra["recovery_count_hedged"] is None
    assert f.extra["count_source"] == "pose_low_detection"
