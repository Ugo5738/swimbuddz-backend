from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.communications_service.templates import club
from services.members_service.routers import clubs
from services.members_service.routers import _club_pricing
from services.members_service.schemas.club import ClubObservedAssessmentUpdate


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", [False, True])
async def test_member_application_response_never_exposes_internal_coach_notes(
    monkeypatch, admin
):
    now = datetime.now(timezone.utc)
    application = clubs.ClubApplication(
        id=uuid4(),
        member_id=uuid4(),
        club_id=uuid4(),
        plan_version_id=uuid4(),
        status="approved",
        community_experience_selected=False,
        approved_payment_modes=["quarterly_prepaid"],
        created_at=now,
        updated_at=now,
    )
    assessment = clubs.ClubReadinessAssessment(
        id=uuid4(),
        application_id=application.id,
        self_report={},
        outcome="club_ready_modified",
        assessor_notes="Private notes",
        primary_technique_focus="Breathing",
        first_club_milestone="25m",
        created_at=now,
        updated_at=now,
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=None),
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: assessment),
                SimpleNamespace(all=lambda: []),
            ]
        ),
    )
    monkeypatch.setattr(_club_pricing, "hydrate_schedules", AsyncMock())
    response = await _club_pricing.application_response(
        application, db, include_internal_notes=admin
    )
    assert response.assessment.assessor_notes == ("Private notes" if admin else None)
    assert response.assessment.primary_technique_focus == "Breathing"
    assert (
        assessment.assessor_notes == "Private notes"
    )  # Never scrub the stored record.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,modes,path,cta",
    [
        (
            "club_ready",
            ["quarterly_prepaid"],
            "/checkout?",
            "Continue Club registration",
        ),
        (
            "club_ready_modified",
            ["transition_per_session"],
            "/checkout?",
            "Continue Club registration",
        ),
        (
            "club_ready",
            ["quarterly_prepaid", "transition_per_session"],
            "/upgrade/club/plan",
            "Continue Club registration",
        ),
        ("academy_first", [], "/upgrade/academy/cohort", "View Academy programmes"),
    ],
)
async def test_branded_result_has_safe_coaching_details_and_correct_cta(
    monkeypatch, outcome, modes, path, cta
):
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(club, "send_email", send)
    monkeypatch.setattr(
        club,
        "get_settings",
        lambda: SimpleNamespace(FRONTEND_URL="https://swimbuddz.com/"),
    )
    await club.send_club_assessment_result_email(
        to_email="ay@example.com",
        member_name="AY <script>",
        club_name="Rowe & Park",
        application_id="application-id",
        outcome=outcome,
        approved_payment_modes=modes,
        transition_expires_at="2026-11-30",
        primary_technique_focus="Breathing <focus>",
        first_club_milestone="Swim 25m & recover",
    )
    _, _, plain, html = send.await_args.args
    assert "swimbuddz-icon-white.png" in html
    assert path in plain and path in html
    assert cta in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "Main coaching focus" in plain and "First goal" in plain
    assert "Breathing &lt;focus&gt;" in html
    assert "₦" not in plain  # Checkout owns the current quote, not the email.
    if len(modes) == 1:
        assert f"application_id=application-id&payment_mode={modes[0]}" in plain
    if modes == ["transition_per_session"]:
        assert "30 November 2026" in plain
        assert "31 December" not in plain
    if outcome == "club_ready_modified":
        assert "with coaching focus" in plain
        assert "modified participation" not in plain


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["club_ready", "club_ready_modified", "academy_first"]
)
@pytest.mark.parametrize("send_result", [False, True])
async def test_assessment_sends_template_without_internal_notes(
    monkeypatch, outcome, send_result
):
    application = SimpleNamespace(id=uuid4(), member_id=uuid4(), club_id=uuid4())
    assessment = SimpleNamespace(result_email_sent_at=None)
    member = SimpleNamespace(first_name="AY", email="ay@example.com")
    db = SimpleNamespace(
        get=AsyncMock(side_effect=[application, member, SimpleNamespace(name="Yaba")]),
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: assessment)
        ),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        add=Mock(),
    )
    client = SimpleNamespace(send_template=AsyncMock(return_value=True))
    monkeypatch.setattr(clubs, "get_email_client", lambda: client)
    monkeypatch.setattr(
        clubs, "_member_for_user", AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    )
    monkeypatch.setattr(clubs, "_application_out", AsyncMock())
    await clubs.complete_observed_club_assessment(
        application.id,
        ClubObservedAssessmentUpdate(
            outcome=outcome,
            assessor_notes="PRIVATE medical context",
            primary_technique_focus="Breathing",
            first_club_milestone="25m",
            send_result_email=send_result,
        ),
        current_user=None,
        db=db,
    )
    assert assessment.assessor_notes == "PRIVATE medical context"
    if send_result:
        data = client.send_template.await_args.kwargs
        assert data["template_type"] == "club_assessment_result"
        assert "assessor_notes" not in data["template_data"]
        assert "PRIVATE" not in str(data)
        assert assessment.result_email_sent_at is not None
    else:
        client.send_template.assert_not_called()
        assert assessment.result_email_sent_at is None
