from unittest.mock import AsyncMock

import pytest
from services.communications_service.templates import session_notifications


@pytest.mark.asyncio
async def test_weekly_digest_renders_operational_and_tier_details(monkeypatch):
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(session_notifications, "send_email", send_email)
    sessions = [
        {
            "id": "session-1",
            "title": "Orca Technique",
            "audience": "club",
            "date": "Saturday, July 25",
            "time": "09:00 AM - 11:00 AM",
            "location": "National Stadium Pool",
            "scope_label": "Orca Pod",
            "leader_label": "Pod Lead: Ada | Coach: Tola",
            "purpose": "Freestyle catch and pacing",
            "weather_text": "Light rain | 27 C | 55% chance",
            "transport_text": "Available from Lekki from NGN 2,000",
            "fee_text": "NGN 3,500",
            "availability_text": "6 spots left",
            "is_booked": False,
            "state_label": "Available for you to book",
            "action_label": "Book session",
            "action_url": "https://api.example.test/tracked-session",
            "calendar_url": "https://calendar.google.com/example",
        }
    ]
    configs = {
        "club": {
            "featured_image_url": "https://cdn.example.test/club.jpg",
            "image_alt": "Club swimmers training",
            "section_intro": "Your pod and general Club sessions.",
            "default_gear_notes": "Bring fins, paddles, goggles, and water.",
        }
    }

    success = await session_notifications.send_weekly_session_digest_email(
        to_email="member@example.com",
        member_name="Wini <script>",
        week_label="July 20 - July 26",
        sessions=sessions,
        digest_configs=configs,
        preferences_url="https://api.example.test/preferences",
    )

    assert success is True
    html = send_email.await_args.args[3]
    for expected in (
        "Club training",
        "Orca Pod",
        "Pod Lead: Ada | Coach: Tola",
        "Light rain | 27 C | 55% chance",
        "Available from Lekki from NGN 2,000",
        "Bring fins, paddles, goggles, and water.",
        "Book session",
        "club.jpg",
    ):
        assert expected in html
    assert "Wini &lt;script&gt;" in html
    assert "Wini <script>" not in html
