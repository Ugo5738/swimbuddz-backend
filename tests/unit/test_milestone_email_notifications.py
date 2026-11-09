from unittest.mock import AsyncMock, patch
import pytest

from services.communications_service.routers.email import (
    TEMPLATE_EMAIL_PREFERENCE,
)
from services.communications_service.templates import academy


@pytest.mark.asyncio
async def test_milestone_approved_email_rendering():
    with patch(
        "services.communications_service.templates.academy.send_email",
        new_callable=AsyncMock,
    ) as mock_send:
        mock_send.return_value = True

        result = await academy.send_milestone_approved_email(
            to_email="isabella@example.com",
            member_name="Isabella",
            milestone_name="Side-Glide Balance",
            program_name="Beginner Freestyle",
            cohort_name="VI 2 - Nov 2026",
            coach_name="Coach T",
            score=95,
            coach_notes="Excellent head position and relaxed breathing!",
            enrollment_id="11111111-2222-3333-4444-555555555555",
        )

        assert result is True
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        to_email, subject, body, html_body = call_args

        assert to_email == "isabella@example.com"
        assert "Side-Glide Balance" in subject
        assert "🎉" in subject
        assert "Isabella" in body
        assert "Coach T" in body
        assert "Excellent head position" in body
        assert "95%" in body
        assert "11111111-2222-3333-4444-555555555555" in body

        # Check HTML body contains the CTA button and details
        assert "Side-Glide Balance" in html_body
        assert "Achieved 🎉" in html_body
        assert "View Your Progress" in html_body
        assert "11111111-2222-3333-4444-555555555555" in html_body


@pytest.mark.asyncio
async def test_milestone_rejected_email_rendering():
    with patch(
        "services.communications_service.templates.academy.send_email",
        new_callable=AsyncMock,
    ) as mock_send:
        mock_send.return_value = True

        result = await academy.send_milestone_rejected_email(
            to_email="isabella@example.com",
            member_name="Isabella",
            milestone_name="Bilateral Breathing",
            program_name="Beginner Freestyle",
            cohort_name="VI 2 - Nov 2026",
            coach_name="Coach T",
            coach_notes="Make sure your lead arm stays extended on the surface while rotating to breathe.",
            enrollment_id="11111111-2222-3333-4444-555555555555",
        )

        assert result is True
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        to_email, subject, body, html_body = call_args

        assert to_email == "isabella@example.com"
        assert "Bilateral Breathing" in subject
        assert "Make sure your lead arm stays extended" in body
        assert "Coach T" in body
        assert "Review Feedback & Resubmit" in html_body
        assert "11111111-2222-3333-4444-555555555555" in html_body


def test_milestone_email_preferences_registered():
    assert "milestone_approved" in TEMPLATE_EMAIL_PREFERENCE
    assert TEMPLATE_EMAIL_PREFERENCE["milestone_approved"] == "email_academy_updates"
    assert "milestone_rejected" in TEMPLATE_EMAIL_PREFERENCE
    assert TEMPLATE_EMAIL_PREFERENCE["milestone_rejected"] == "email_academy_updates"
