"""No-API test for _apply_borderline_honesty — on a BORDERLINE clip the coach must
go quiet: drop the per-frame evidence citations (the founder saw a confident "high
elbow recovery" on a frame the swimmer hadn't started stroking), demote "can't see
X" out of strengths, and cap confidence. CLEAN clips are untouched."""

from __future__ import annotations

from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    SEVERITY_INFO,
    SEVERITY_STRENGTH,
    ComponentResult,
    Finding,
    FrameRef,
    GateTier,
    InputProfile,
    PipelineResult,
)
from services.ai_service.tasks.analyze import _apply_borderline_honesty


def _result(tier: GateTier) -> PipelineResult:
    findings = [
        Finding(
            component="recovery_coach",
            observation="The frames show a high elbow recovery.",
            severity=SEVERITY_STRENGTH,
            confidence=0.9,
            evidence_frames=[FrameRef(index=3, timestamp_s=1.2)],
        ),
        Finding(
            component="holistic_coach",
            observation="The waterline across the body is not visible.",
            severity=SEVERITY_STRENGTH,
            confidence=0.8,
            evidence_frames=[FrameRef(index=5, timestamp_s=2.0)],
        ),
        Finding(
            component="recovery_coach",
            observation="Your elbow drops on the recovery.",
            severity=SEVERITY_FIX,
            confidence=0.85,
            evidence_frames=[FrameRef(index=7, timestamp_s=3.1)],
        ),
    ]
    return PipelineResult(
        input_profile=InputProfile.UNKNOWN,
        gate_tier=tier,
        results=[ComponentResult(component="coach", findings=findings)],
    )


def test_borderline_drops_evidence_demotes_cant_see_and_caps_confidence():
    res = _result(GateTier.BORDERLINE)
    _apply_borderline_honesty(res)
    fs = res.results[0].findings
    assert all(
        f.evidence_frames == [] for f in fs
    )  # no "watch this moment" we can't trust
    assert all(f.confidence <= 0.4 for f in fs)  # confidence capped
    # "not visible" strength demoted out of strengths; the real fix stays a fix
    cant_see = next(f for f in fs if "not visible" in f.observation)
    assert cant_see.severity == SEVERITY_INFO
    assert next(f for f in fs if "drops" in f.observation).severity == SEVERITY_FIX
    # a substantive strength stays a strength (just quieter), not deleted
    assert (
        next(f for f in fs if "high elbow" in f.observation).severity
        == SEVERITY_STRENGTH
    )


def test_clean_clip_is_untouched():
    res = _result(GateTier.CLEAN)
    _apply_borderline_honesty(res)
    fs = res.results[0].findings
    assert fs[0].evidence_frames and fs[0].confidence == 0.9  # nothing changed
    assert fs[1].severity == SEVERITY_STRENGTH
