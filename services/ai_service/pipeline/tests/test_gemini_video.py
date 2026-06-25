"""No-API checks for the Gemini video-coach swap.

The video system prompt must be the SAME stills rubric with only the medium
language flipped (it's watching video → it CAN see motion, and cites moments by
TIMESTAMP not frame index), and a timestamp citation must map to the nearest
extracted frame so the evidence-thumbnail machinery still resolves.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.ai_service.coach.prompt import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_VIDEO,
    build_user_prompt_video,
)
from services.ai_service.pipeline.components.holistic_coach import (
    _evidence_frames_video,
)


def test_video_prompt_flips_the_stills_language():
    # the stills coach still says stills...
    assert "STILL FRAMES (not video)" in SYSTEM_PROMPT
    assert "CANNOT see movement between frames" in SYSTEM_PROMPT
    # ...the video coach does NOT (every swap applied), and is motion-aware
    assert "STILL FRAMES (not video)" not in SYSTEM_PROMPT_VIDEO
    assert "CANNOT see movement between frames" not in SYSTEM_PROMPT_VIDEO
    assert "SHORT VIDEO" in SYSTEM_PROMPT_VIDEO
    assert "cite the TIMESTAMP" in SYSTEM_PROMPT_VIDEO
    # the shared honesty rubric survives the swap untouched
    assert "HONESTY OVER HELPFULNESS" in SYSTEM_PROMPT_VIDEO
    assert "NO FALSE PRAISE" in SYSTEM_PROMPT_VIDEO


def test_video_user_prompt_mentions_video_and_timestamps():
    p = build_user_prompt_video("freestyle")
    assert "video" in p.lower()
    assert "t=2.1s" in p


@dataclass
class _F:
    index: int
    timestamp_s: float


def test_timestamp_citation_maps_to_nearest_frame():
    frames = [_F(0, 0.0), _F(1, 1.0), _F(2, 2.0), _F(3, 3.0)]
    refs = _evidence_frames_video("at t=2.1s: the elbow drops before the pull", frames)
    assert len(refs) == 1
    assert refs[0].index == 2  # 2.0s is nearest to 2.1s
    assert refs[0].timestamp_s == 2.0


def test_no_timestamp_yields_no_evidence():
    frames = [_F(0, 0.0), _F(1, 1.0)]
    assert _evidence_frames_video("the elbow drops on recovery", frames) == []
