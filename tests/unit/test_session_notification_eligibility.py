from datetime import datetime, timedelta, timezone

from services.communications_service.tasks.session_notifications import (
    _has_paid_session_access,
    _is_unpaid_community_prospect,
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


def test_paid_club_or_academy_member_can_get_club_booking_prompt():
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


def test_expired_paid_until_does_not_grant_booking_prompt():
    member = _member(community_paid_until=(NOW - timedelta(days=1)).isoformat())

    assert not _has_paid_session_access(member, "community", NOW)
    assert _is_unpaid_community_prospect(member, NOW)


def test_cohort_class_access_is_decided_by_enrollment_lookup():
    member = _member()

    assert _has_paid_session_access(member, "cohort_class", NOW)
