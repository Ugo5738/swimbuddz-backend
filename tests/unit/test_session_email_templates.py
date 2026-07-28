from unittest.mock import AsyncMock

import pytest

from services.communications_service.templates import session_notifications


def _session_context() -> dict:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "Saturday Club Swim (The Orcas)",
        "session_type": "club",
        "audience": "club",
        "featured_image_url": "https://cdn.example.test/club.jpg",
        "image_alt": "Club members practising in lanes",
        "section_intro": "Your pod practice and general Club sessions.",
        "gear_notes": "Bring goggles, cap, towel, water, fins, and paddles.",
        "date": "Saturday, August 01, 2026",
        "time": "09:00 AM",
        "location": "Rowe Park Pool",
        "address": "Herbert Macaulay Way",
        "scope_label": "orcas Pod",
        "leader_label": (
            "Pod Lead: Ugochukwu Nwachukwu | " "Assistant Pod Lead: Comfort Uwaheren"
        ),
        "purpose": "Freestyle pacing and turns",
        "weather_summary": {
            "condition_text": "Light rain",
            "temperature_text": "28°C",
            "rain_chance_text": "55% chance of rain",
            "rainfall_text": "~1mm during session",
            "explanation": "Light rain likely - warm and swimmable.",
        },
        "weather_text": (
            "Light rain | 28°C | 55% chance of rain. "
            "Light rain likely - warm and swimmable."
        ),
        "transport_text": (
            "Transport available from Ago Palace Way via Stop 1 from NGN 5,000"
        ),
        "pool_fee": 5000,
        "fee_text": "NGN 5,000",
        "capacity": 20,
        "occupied_slots": 7,
        "remaining_spots": 13,
        "availability_text": "13 spots left",
        "calendar_url": "https://calendar.google.com/example",
        "is_booked": False,
        "state_label": "Available for you to book",
        "action_label": "Book session",
    }


@pytest.mark.asyncio
async def test_booking_prompt_uses_shared_rich_session_context(monkeypatch):
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(session_notifications, "send_email", send_email)

    success = await session_notifications.send_session_announcement_email(
        to_email="member@example.com",
        member_name="Member <name>",
        session=_session_context(),
    )

    assert success is True
    subject, body, html = send_email.await_args.args[1:]
    assert "New Club Session" in subject
    for expected in (
        "orcas Pod",
        "Pod Lead: Ugochukwu Nwachukwu",
        "Transport available from Ago Palace Way",
        "13 spots left",
        "NGN 5,000",
        "Bring goggles, cap, towel, water, fins, and paddles.",
    ):
        assert expected in body
        assert expected in html
    assert "club.jpg" in html
    assert "Light rain" in html
    assert "Member &lt;name&gt;" in html
    assert "Member <name>" not in html


@pytest.mark.asyncio
async def test_daily_follow_up_is_not_labelled_as_a_new_session(monkeypatch):
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(session_notifications, "send_email", send_email)

    await session_notifications.send_session_announcement_email(
        to_email="member@example.com",
        member_name="Member",
        session=_session_context(),
        is_follow_up=True,
    )

    subject, body = send_email.await_args.args[1:3]
    assert subject.startswith("Book your Club Session:")
    assert "still available to book" in body


@pytest.mark.asyncio
async def test_24h_reminder_is_compact_but_keeps_preparation_context(monkeypatch):
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(session_notifications, "send_email", send_email)
    session = _session_context()
    session["is_booked"] = True

    await session_notifications.send_session_reminder_email(
        to_email="member@example.com",
        member_name="Member",
        session=session,
        reminder_type="24h",
    )

    html = send_email.await_args.args[3]
    for expected in (
        "club.jpg",
        "orcas Pod",
        "Light rain",
        "Transport available from Ago Palace Way",
        "Bring goggles, cap, towel, water, fins, and paddles.",
    ):
        assert expected in html


@pytest.mark.asyncio
@pytest.mark.parametrize("reminder_type", ["3h", "1h"])
async def test_last_minute_reminders_omit_image_and_keep_essentials(
    monkeypatch,
    reminder_type,
):
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(session_notifications, "send_email", send_email)

    await session_notifications.send_session_reminder_email(
        to_email="member@example.com",
        member_name="Member",
        session=_session_context(),
        reminder_type=reminder_type,
    )

    html = send_email.await_args.args[3]
    assert "club.jpg" not in html
    assert "Bring goggles" not in html
    assert "Light rain" in html
    assert "Transport available from Ago Palace Way" in html
    assert "Rowe Park Pool" in html
