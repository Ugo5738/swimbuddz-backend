"""Unit tests for the goal-aware prompt clause (no API).

build_goal_block flavours wording per discipline but must always carry the honesty
fence and must add NOTHING for the plain default (so today's behaviour is byte-
identical).

Run: PYTHONPATH=. .venv/bin/python -m pytest \
        services/ai_service/pipeline/tests/test_rubric.py -q
"""

from __future__ import annotations

from services.ai_service.coach.rubric import build_goal_block
from services.ai_service.pipeline.types import CoachContext

FENCE = "MUST NOT"  # the honesty fence appears in every non-empty block


def test_plain_default_adds_nothing():
    assert build_goal_block(CoachContext()) == ""
    assert build_goal_block(None) == ""
    assert build_goal_block(CoachContext(discipline="general")) == ""


def test_sprint_is_fenced_and_warns_against_inventing_a_dead_spot():
    block = build_goal_block(CoachContext(discipline="sprint"))
    assert FENCE in block
    assert "SPRINT" in block
    assert "dead spot" in block.lower()  # the explicit anti-hallucination guard


def test_distance_leans_efficiency_and_is_fenced():
    block = build_goal_block(CoachContext(discipline="distance"))
    assert FENCE in block
    assert "DISTANCE" in block
    assert "efficien" in block.lower()


def test_focus_area_is_mentioned_and_honest():
    block = build_goal_block(
        CoachContext(discipline="general", focus_area="head_breath")
    )
    assert block  # focus alone makes the block non-empty even for general
    assert "breathing" in block.lower()
    assert "cannot see" in block.lower()  # told to be honest if not visible


def test_goal_text_is_fenced_as_tone_only_and_clamped():
    long_goal = "swim faster " * 50  # > 200 chars
    block = build_goal_block(CoachContext(discipline="general", goal_text=long_goal))
    assert "TONE ONLY" in block
    # the quoted goal text is clamped to <= 200 chars
    quoted = block.split('"')[1]
    assert len(quoted) <= 200


def test_invalid_discipline_falls_back_to_general_wording():
    # an unknown discipline with no extras adds nothing (treated as general)
    assert build_goal_block(CoachContext(discipline="freestyle_relay")) == ""
