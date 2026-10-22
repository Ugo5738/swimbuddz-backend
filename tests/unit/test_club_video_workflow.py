"""Club video evidence must never bypass human safety approval."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.members_service.routers import clubs
from services.members_service.schemas.club import (
    ClubFirstSessionVerification,
    ClubObservedAssessmentUpdate,
    ClubVideoAssessmentSubmit,
)


def make_video_submission():
    return ClubVideoAssessmentSubmit(
        swim_video_media_id=uuid4(),
        tread_video_media_id=uuid4(),
        recorded_in_supervised_pool=True,
        authentic_uncut_footage=True,
    )


@pytest.mark.asyncio
async def test_video_submission_is_held_for_assessor_and_media_verified(monkeypatch):
    member = SimpleNamespace(id=uuid4())
    application = SimpleNamespace(
        id=uuid4(),
        member_id=member.id,
        status="assessment_pending",
    )
    assessment = SimpleNamespace(self_report={"can_swim_25m_continuously": True})
    db = SimpleNamespace(
        get=AsyncMock(return_value=application),
        scalar=AsyncMock(return_value=assessment),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    video_verify = AsyncMock()
    acknowledge = AsyncMock()
    monkeypatch.setattr(clubs, "notify_assessment_submission", acknowledge)
    monkeypatch.setattr(clubs, "_member_for_user", AsyncMock(return_value=member))
    monkeypatch.setattr(clubs, "_verify_club_video", video_verify)
    monkeypatch.setattr(clubs, "_application_out", AsyncMock(return_value=application))
    body = make_video_submission()

    result = await clubs.submit_club_video_assessment(
        application.id,
        body,
        current_user=SimpleNamespace(user_id=uuid4()),
        db=db,
    )

    assert result.status == "assessment_pending"
    assert assessment.self_report["assessment_method"] == "video"
    assert assessment.self_report["video_review_status"] == "submitted"
    assert assessment.self_report["swim_video_media_id"] == str(
        body.swim_video_media_id
    )
    assert video_verify.await_count == 2
    acknowledge.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_video_submission_rejects_assessor_required_in_person(monkeypatch):
    member = SimpleNamespace(id=uuid4())
    application = SimpleNamespace(
        id=uuid4(),
        member_id=member.id,
        status="assessment_pending",
    )
    assessment = SimpleNamespace(
        self_report={"video_review_status": "in_person_required"}
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=application),
        scalar=AsyncMock(return_value=assessment),
    )
    monkeypatch.setattr(clubs, "_member_for_user", AsyncMock(return_value=member))
    with pytest.raises(HTTPException) as exc:
        await clubs.submit_club_video_assessment(
            application.id,
            make_video_submission(),
            current_user=SimpleNamespace(user_id=uuid4()),
            db=db,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_video_approval_requires_observed_skill_checks(monkeypatch):
    application = SimpleNamespace(
        id=uuid4(),
        member_id=uuid4(),
        club_id=uuid4(),
        status="assessment_pending",
    )
    assessment = SimpleNamespace(
        self_report={
            "assessment_method": "video",
            "video_review_status": "submitted",
            "swim_video_media_id": str(uuid4()),
            "tread_video_media_id": str(uuid4()),
        }
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=application),
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: assessment)
        ),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        clubs,
        "_member_for_user",
        AsyncMock(return_value=SimpleNamespace(id=uuid4())),
    )
    body = ClubObservedAssessmentUpdate(
        outcome="club_ready",
        assessment_method="video",
        observed_checks={"continuous_25m": True},
        send_result_email=False,
    )

    with pytest.raises(HTTPException) as exc:
        await clubs.complete_observed_club_assessment(
            application.id,
            body,
            current_user=None,
            db=db,
        )
    assert exc.value.status_code == 422
    db.commit.assert_not_awaited()
    assert application.status == "assessment_pending"


@pytest.mark.asyncio
async def test_first_session_check_tracks_failed_verification(monkeypatch):
    application = SimpleNamespace(
        id=uuid4(),
        status="enrolled",
    )
    assessment = SimpleNamespace(
        self_report={
            "assessment_method": "video",
            "first_session_check_required": True,
        }
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=application),
        scalar=AsyncMock(return_value=assessment),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    monkeypatch.setattr(clubs, "_application_out", AsyncMock(return_value=application))

    await clubs.verify_club_first_session(
        application.id,
        ClubFirstSessionVerification(
            verified=False,
            notes="Needs close coaching and repeat assessment",
        ),
        _admin=None,
        db=db,
    )
    assert assessment.self_report["first_session_check_result"] == "needs_follow_up"
    assert assessment.self_report["first_session_check_required"] is True
    db.commit.assert_awaited_once()
