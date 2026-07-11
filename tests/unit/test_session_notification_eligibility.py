from datetime import datetime, timedelta, timezone

import pytest

from services.communications_service.tasks.session_notifications import (
    _default_booking_prompt_tier,
    _get_session_announcement_members,
    _has_paid_session_access,
    _is_unpaid_community_prospect,
    _summarize_session_weather,
)

NOW = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)


def _member(**overrides):
    data = {
        "active_tiers": ["community"],
        "primary_tier": "community",
        "community_paid_until": None,
        "club_paid_until": None,
        "academy_paid_until": None,
    }
    data.update(overrides)
    return data


def test_unpaid_baseline_community_member_is_prospect_not_booking_recipient():
    member = _member()

    assert not _has_paid_session_access(member, "community", NOW)
    assert _is_unpaid_community_prospect(member, NOW)


def test_paid_community_member_can_get_community_booking_prompt():
    member = _member(community_paid_until=(NOW + timedelta(days=30)).isoformat())

    assert _has_paid_session_access(member, "community", NOW)
    assert not _is_unpaid_community_prospect(member, NOW)


def test_paid_club_or_academy_member_has_club_booking_access():
    club_member = _member(
        active_tiers=["club", "community"],
        primary_tier="club",
        club_paid_until=(NOW + timedelta(days=30)).isoformat(),
    )
    academy_member = _member(
        active_tiers=["academy", "club", "community"],
        primary_tier="academy",
        academy_paid_until=(NOW + timedelta(days=30)).isoformat(),
    )

    assert _has_paid_session_access(club_member, "club", NOW)
    assert _has_paid_session_access(academy_member, "club", NOW)


def test_default_booking_prompt_tier_uses_highest_paid_membership():
    future = (NOW + timedelta(days=30)).isoformat()

    assert (
        _default_booking_prompt_tier(
            _member(
                active_tiers=["club", "community"],
                primary_tier="club",
                club_paid_until=future,
                community_paid_until=future,
            ),
            NOW,
        )
        == "club"
    )
    assert (
        _default_booking_prompt_tier(
            _member(
                active_tiers=["academy", "club", "community"],
                primary_tier="academy",
                academy_paid_until=future,
                club_paid_until=future,
                community_paid_until=future,
            ),
            NOW,
        )
        == "academy"
    )


@pytest.mark.asyncio
async def test_community_prompt_targets_default_community_and_prospects_only():
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    members = [
        _member(id="community", community_paid_until=future),
        _member(id="prospect"),
        _member(
            id="club",
            active_tiers=["club", "community"],
            primary_tier="club",
            club_paid_until=future,
            community_paid_until=future,
        ),
        _member(
            id="academy",
            active_tiers=["academy", "club", "community"],
            primary_tier="academy",
            academy_paid_until=future,
            club_paid_until=future,
            community_paid_until=future,
        ),
    ]

    recipients = await _get_session_announcement_members(
        session={"session_type": "community"},
        active_members=members,
    )

    assert {m["id"] for m in recipients} == {"community", "prospect"}


@pytest.mark.asyncio
async def test_club_prompt_targets_default_club_members_only():
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    members = [
        _member(id="community", community_paid_until=future),
        _member(
            id="club",
            active_tiers=["club", "community"],
            primary_tier="club",
            club_paid_until=future,
            community_paid_until=future,
        ),
        _member(
            id="academy",
            active_tiers=["academy", "club", "community"],
            primary_tier="academy",
            academy_paid_until=future,
            club_paid_until=future,
            community_paid_until=future,
        ),
        _member(id="prospect"),
    ]

    recipients = await _get_session_announcement_members(
        session={"session_type": "club"},
        active_members=members,
    )

    assert {m["id"] for m in recipients} == {"club"}


def test_expired_paid_until_does_not_grant_booking_prompt():
    member = _member(community_paid_until=(NOW - timedelta(days=1)).isoformat())

    assert not _has_paid_session_access(member, "community", NOW)
    assert _is_unpaid_community_prospect(member, NOW)


def test_cohort_class_access_is_decided_by_enrollment_lookup():
    member = _member()

    assert _has_paid_session_access(member, "cohort_class", NOW)


def test_summarize_session_weather_uses_session_hours_only():
    forecast = {
        "hourly": {
            "time": [
                "2026-07-18T10:00",
                "2026-07-18T11:00",
                "2026-07-18T12:00",
                "2026-07-18T13:00",
                "2026-07-18T14:00",
            ],
            "precipitation_probability": [90, 20, 65, 40, 10],
            "precipitation": [5.0, 0.0, 0.7, 0.5, 0.0],
            "temperature_2m": [26.0, 27.0, 28.4, 29.0, 25.0],
            "weather_code": [65, 3, 63, 61, 1],
        }
    }

    summary = _summarize_session_weather(
        forecast,
        starts_at=datetime(2026, 7, 18, 11, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 7, 18, 13, 0, tzinfo=timezone.utc),
    )

    assert summary == {
        "condition_text": "Rain",
        "temperature_text": "29°C",
        "rain_chance_text": "65% chance of rain",
        "rainfall_text": "~1.2mm during session",
        "explanation": "Light rain likely - warm and swimmable.",
    }


def test_summarize_session_weather_returns_none_without_matching_hours():
    forecast = {"hourly": {"time": ["2026-07-18T09:00"]}}

    assert (
        _summarize_session_weather(
            forecast,
            starts_at=datetime(2026, 7, 18, 11, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 7, 18, 13, 0, tzinfo=timezone.utc),
        )
        is None
    )
