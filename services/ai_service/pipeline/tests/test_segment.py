"""Unit tests for the deterministic phase-grouping core (no API, no cv2).

These prove the GROUPING/COUNTING logic independent of the VLM classifier: given a
frame-label sequence, grouping must segment every phase, split recovery per arm,
merge splash doubles, smooth, and break correctly. If the golden-set count is later
wrong despite these passing, the fault is the classifier, not the grouper — which
is the whole reason this half is pure.

Run: PYTHONPATH=. .venv/bin/python -m pytest \
        services/ai_service/pipeline/tests/test_segment.py -q
"""

from __future__ import annotations

from services.ai_service.pipeline.segment import FrameLabel, group_phase_instances

G, R, ND, E, B = "glide_extension", "recovery", "indeterminate", "entry", "breath"
REC_ONLY = ("recovery",)  # isolate recovery for the counting assertions


def _ts(n, dt=0.2):
    return [round(i * dt, 3) for i in range(n)]


def _labels(seq, arm="near"):
    """Build FrameLabels: recovery frames get ``arm``, everything else 'none'."""
    return [
        FrameLabel(index=i, phase=p, arm=(arm if p == R else "none"), conf=0.9)
        for i, p in enumerate(seq)
    ]


def test_empty_and_mismatch():
    assert group_phase_instances([], []) == []
    assert group_phase_instances(_labels([R, R]), [0.0]) == []  # length mismatch → []


def test_counts_two_distinct_recoveries():
    seq = [G, R, R, G, G, R, R, G]  # two recovery runs, well separated
    out = group_phase_instances(
        _labels(seq), _ts(len(seq), 0.2), min_period_s=0.5, segment_phases=REC_ONLY
    )
    assert len(out) == 2
    assert [o.instance_id for o in out] == [0, 1]


def test_single_frame_flip_is_smoothed_away():
    # one stray glide frame inside a recovery → mode-smooth fuses it → ONE recovery
    seq = [R, R, G, R, R]
    out = group_phase_instances(
        _labels(seq), _ts(len(seq), 0.2), segment_phases=REC_ONLY
    )
    assert len(out) == 1


def test_close_doubles_merge_not_split():
    # two short recovery runs split by a brief glide, gap < min_period → merge to 1
    seq = [R, R, G, G, R, R]
    out = group_phase_instances(
        _labels(seq),
        _ts(len(seq), 0.1),
        min_period_s=0.5,
        smooth=False,
        segment_phases=REC_ONLY,
    )
    assert len(out) == 1


def test_long_pause_breaks_the_chain():
    # the seg81 "pause to sky" case: a long non-recovery gap keeps them separate
    seq = [R, R, G, G, G, G, G, R, R]
    out = group_phase_instances(
        _labels(seq),
        _ts(len(seq), 0.5),
        min_period_s=0.5,
        smooth=False,
        segment_phases=REC_ONLY,
    )
    assert len(out) == 2


def test_alternating_arms_stay_separate():
    # near then far recovery, delimited by a glide (as every real stroke is) — both
    # are kept as separate chunks; the far arm is NOT discarded.
    labels = [
        FrameLabel(0, R, "near", conf=0.9),
        FrameLabel(1, R, "near", conf=0.9),
        FrameLabel(2, G, "none", conf=0.9),
        FrameLabel(3, R, "far", conf=0.9),
        FrameLabel(4, R, "far", conf=0.9),
    ]
    out = group_phase_instances(
        labels, _ts(5, 0.1), min_period_s=0.5, smooth=False, segment_phases=REC_ONLY
    )
    assert len(out) == 2
    assert {o.arm for o in out} == {"near", "far"}


def test_indeterminate_does_not_become_recovery():
    seq = [ND, ND, R, R, ND, ND]
    out = group_phase_instances(
        _labels(seq), _ts(len(seq), 0.3), min_period_s=0.5, segment_phases=REC_ONLY
    )
    assert len(out) == 1
    assert out[0].phase.value == "recovery"


def test_all_phases_are_segmented():
    # default segment_phases → recovery + entry + glide + breath all become chunks;
    # nothing is discarded. smooth=False so single-frame entry/breath aren't fused.
    seq = [G, R, R, E, G, B, R, R]
    out = group_phase_instances(_labels(seq), _ts(len(seq), 0.2), smooth=False)
    phases = {o.phase.value for o in out}
    assert phases == {"recovery", "entry", "glide", "breath"}
    assert sum(1 for o in out if o.phase.value == "recovery") == 2


def test_bounds_are_time_based_and_ordered():
    seq = [G, R, R, G, R, G]
    out = group_phase_instances(
        _labels(seq), _ts(len(seq), 0.25), min_period_s=0.3, segment_phases=REC_ONLY
    )
    for o in out:
        assert o.end_s >= o.start_s
        assert o.start_s <= o.peak_s <= o.end_s
