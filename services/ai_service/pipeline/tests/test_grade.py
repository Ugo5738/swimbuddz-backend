"""Exhaustive unit tests for the deterministic discipline re-grade (no API).

grade() is the testable heart of goal-awareness — the SAME honest verdict must map
to the right (severity, rank) per discipline, and must NEVER suppress a visible
fault or invent one. These tests pin the §12.3 matrix.

Run: PYTHONPATH=. .venv/bin/python -m pytest \
        services/ai_service/pipeline/tests/test_grade.py -q
"""

from __future__ import annotations

import pytest

from services.ai_service.pipeline.grade import _GRADES, grade
from services.ai_service.pipeline.types import (
    SEVERITY_FIX,
    SEVERITY_INFO,
    SEVERITY_STRENGTH,
    CoachContext,
)

SPRINT = CoachContext(discipline="sprint")
DISTANCE = CoachContext(discipline="distance")
GENERAL = CoachContext(discipline="general")
ALL_DISC = (SPRINT, DISTANCE, GENERAL)
VALID_SEVERITIES = {SEVERITY_FIX, SEVERITY_STRENGTH, SEVERITY_INFO}


def sev(aspect, verdict, ctx):
    return grade(aspect, verdict, ctx)[0]


def rank(aspect, verdict, ctx):
    return grade(aspect, verdict, ctx)[1]


# ── the headline goal-awareness flip ──────────────────────────────────────────
def test_long_extension_flips_distance_strength_vs_sprint_info():
    assert sev("entry_reach", "clean_extended", DISTANCE) == SEVERITY_STRENGTH
    assert sev("entry_reach", "clean_extended", GENERAL) == SEVERITY_STRENGTH
    # for a sprinter a long held reach is only a hedged INFO (dead-spot lives
    # between frames) — never a hard FIX
    assert sev("entry_reach", "clean_extended", SPRINT) == SEVERITY_INFO


def test_short_reach_flips_opposite():
    assert sev("entry_reach", "short", SPRINT) == SEVERITY_INFO  # fine for tempo
    assert sev("entry_reach", "short", DISTANCE) == SEVERITY_FIX
    assert sev("entry_reach", "short", GENERAL) == SEVERITY_FIX


def test_recovery_wide_demoted_for_sprint():
    assert sev("recovery_elbow", "wide", SPRINT) == SEVERITY_INFO
    assert sev("recovery_elbow", "wide", DISTANCE) == SEVERITY_FIX
    assert sev("recovery_elbow", "wide", GENERAL) == SEVERITY_FIX


# ── discipline-neutral faults: same severity for everyone ──────────────────────
@pytest.mark.parametrize(
    "aspect,verdict",
    [
        ("head_breath", "lifted"),
        ("recovery_elbow", "dropped"),
        ("body_line", "hips_low"),
        ("body_line", "legs_low"),
        ("body_line", "piked"),
        ("body_line", "arched"),
    ],
)
def test_neutral_faults_are_fix_for_every_discipline(aspect, verdict):
    assert {sev(aspect, verdict, c) for c in ALL_DISC} == {SEVERITY_FIX}


def test_body_line_sink_outranks_for_distance_but_not_sprint():
    # same FIX severity, but distance ranks it top while sprint puts propulsion first
    assert rank("body_line", "hips_low", DISTANCE) < rank(
        "body_line", "hips_low", SPRINT
    )
    assert rank("head_breath", "lifted", GENERAL) == 1  # highest-impact adult fault


# ── strengths + observations ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    "aspect,verdict",
    [("recovery_elbow", "high"), ("body_line", "flat"), ("head_breath", "neutral")],
)
def test_clean_technique_is_strength_for_every_discipline(aspect, verdict):
    assert {sev(aspect, verdict, c) for c in ALL_DISC} == {SEVERITY_STRENGTH}


@pytest.mark.parametrize("side", ["left", "right", "both", "none_seen"])
def test_breath_side_is_never_a_fault(side):
    # breathing side is an observation, never a FIX — for any discipline
    assert {sev("head_breath", side, c) for c in ALL_DISC} == {SEVERITY_INFO}


def test_overreach_is_info_never_crossover_fix():
    for c in ALL_DISC:
        assert sev("entry_reach", "overreach", c) == SEVERITY_INFO


# ── honesty / safety invariants ───────────────────────────────────────────────
def test_unknown_aspect_or_verdict_is_safe_info_never_fix():
    assert grade("nonsense", "whatever", GENERAL) == (SEVERITY_INFO, 70)
    assert sev("body_line", "made_up_verdict", SPRINT) == SEVERITY_INFO


def test_invalid_discipline_falls_back_to_general():
    weird = CoachContext(discipline="butterfly_sprint")
    assert grade("entry_reach", "clean_extended", weird) == grade(
        "entry_reach", "clean_extended", GENERAL
    )


def test_every_grade_returns_a_valid_severity_and_rank():
    # exhaustive sweep: no (aspect, verdict, discipline) combination may produce an
    # invalid severity or a negative rank — nothing falls through to a hidden state.
    for aspect, verdict in _GRADES:
        for ctx in ALL_DISC:
            severity, r = grade(aspect, verdict, ctx)
            assert severity in VALID_SEVERITIES
            assert r >= 0


# ── focus_area bump ───────────────────────────────────────────────────────────
def test_focus_area_lifts_its_aspect_to_top_without_changing_severity():
    base_sev, base_rank = grade("entry_reach", "short", DISTANCE)  # a FIX, rank 3
    focused = CoachContext(discipline="distance", focus_area="entry_reach")
    f_sev, f_rank = grade("entry_reach", "short", focused)
    assert f_sev == base_sev  # severity (honesty) unchanged
    assert f_rank == 0 and f_rank < base_rank  # but lifted to the very top


def test_focus_area_does_not_touch_other_aspects():
    focused = CoachContext(discipline="distance", focus_area="entry_reach")
    assert grade("body_line", "hips_low", focused) == grade(
        "body_line", "hips_low", DISTANCE
    )


def test_focus_on_info_aspect_is_surfaced_but_not_forced_to_zero():
    focused = CoachContext(discipline="sprint", focus_area="entry_reach")
    # clean_extended is INFO for sprint; focus surfaces it (rank <= 3) but it stays INFO
    f_sev, f_rank = grade("entry_reach", "clean_extended", focused)
    assert f_sev == SEVERITY_INFO and f_rank <= 3
