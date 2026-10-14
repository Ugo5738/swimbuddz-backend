"""Contract tests for private video-based Club readiness."""

import uuid

import pytest
from pydantic import ValidationError

from services.members_service.schemas.club import (
    ClubVideoAssessmentSubmit,
    ClubVideoAssessmentFeedback,
    ClubFirstSessionVerification,
    ClubObservedAssessmentUpdate,
)
from services.media_service.services.storage import BucketType, get_bucket_for_purpose


def test_club_video_upload_is_private():
    assert get_bucket_for_purpose("club_assessment_video") == BucketType.PRIVATE


def test_video_submission_requires_authentic_safe_distinct_clips():
    a, b = uuid.uuid4(), uuid.uuid4()
    valid = ClubVideoAssessmentSubmit(
        swim_video_media_id=a,
        tread_video_media_id=b,
        recorded_in_supervised_pool=True,
        authentic_uncut_footage=True,
    )
    assert valid.swim_video_media_id == a
    for overrides in [
        {"tread_video_media_id": a},
        {"recorded_in_supervised_pool": False},
        {"authentic_uncut_footage": False},
    ]:
        payload = valid.model_dump()
        payload.update(overrides)
        with pytest.raises(ValidationError):
            ClubVideoAssessmentSubmit.model_validate(payload)


@pytest.mark.parametrize("decision", ["request_another_video", "in_person_required"])
def test_review_feedback_requires_reason(decision):
    with pytest.raises(ValidationError):
        ClubVideoAssessmentFeedback(decision=decision, notes="No")
    result = ClubVideoAssessmentFeedback(decision=decision, notes="Please try again")
    assert result.decision == decision


def test_first_session_check_requires_observation():
    with pytest.raises(ValidationError):
        ClubFirstSessionVerification(verified=True, notes="OK")


def test_academy_first_has_no_club_payment_options():
    outcome = ClubObservedAssessmentUpdate(
        outcome="academy_first",
        approved_payment_modes=[],
    )
    assert outcome.approved_payment_modes == []
    with pytest.raises(ValidationError):
        ClubObservedAssessmentUpdate(
            outcome="club_ready",
            assessment_method="video",
            approved_payment_modes=[],
        )
