from datetime import datetime, timedelta, timezone

from services.members_service.models import MemberMembership
from services.members_service.tasks.membership_renewals import (
    _candidate_expiries,
    reminder_delivery_key,
    reminder_offsets,
)


def test_quarterly_club_uses_short_cycle_schedule():
    assert reminder_offsets("club", 3) == (14, 7, 3, 1, 0, -3)


def test_long_club_and_community_use_annual_schedule():
    assert reminder_offsets("club", 6) == (30, 14, 7, 1, 0, -7)
    assert reminder_offsets("community") == (30, 14, 7, 1, 0, -7)


def test_academy_promotes_continuation_without_post_expiry_chase():
    assert reminder_offsets("academy") == (14, 7, 1, 0)


def test_post_academy_bridge_uses_short_club_reminder_cycle():
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    membership = MemberMembership(
        club_paid_until=now - timedelta(days=1),
        post_academy_club_until=now + timedelta(days=30),
        club_billing_cycle_months=12,
    )

    club_candidate = _candidate_expiries(membership)[1]

    assert club_candidate == ("club", now + timedelta(days=30), 1)


def test_dated_club_enrollment_drives_quarterly_renewal_reminder():
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    enrollment_expiry = now + timedelta(days=28)
    membership = MemberMembership(
        club_paid_until=now - timedelta(days=1),
        club_billing_cycle_months=12,
    )

    club_candidate = _candidate_expiries(membership, enrollment_expiry)[1]

    assert club_candidate == ("club", enrollment_expiry, 3)


def test_reminder_keys_are_channel_idempotent_and_reset_after_renewal():
    expiry = datetime(2027, 2, 7, tzinfo=timezone.utc)
    renewed_expiry = datetime(2028, 2, 7, tzinfo=timezone.utc)

    email_key = reminder_delivery_key("community", expiry, 30, "email")

    assert email_key == reminder_delivery_key("community", expiry, 30, "email")
    assert email_key != reminder_delivery_key("community", expiry, 30, "in_app")
    assert email_key != reminder_delivery_key("community", renewed_expiry, 30, "email")
