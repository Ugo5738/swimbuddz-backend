from datetime import datetime, timedelta, timezone

from services.members_service.services.membership_status import (
    build_membership_status_summary,
)

NOW = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)
FUTURE = NOW + timedelta(days=30)
PAST = NOW - timedelta(days=1)


def _summary(**overrides):
    data = {
        "primary_tier": "community",
        "active_tiers": ["community"],
        "declared_tiers": ["community"],
        "requested_tiers": None,
        "community_paid_until": None,
        "club_paid_until": None,
        "academy_paid_until": None,
        "post_academy_club_until": None,
        "pending_payment_reference": None,
        "pending_tier_payments": {},
        "now": NOW,
    }
    data.update(overrides)
    return build_membership_status_summary(**data)


def test_unpaid_approved_community_is_not_a_paid_member():
    summary = _summary()

    assert summary["paid_tier"] == "prospect"
    assert summary["display_label"] == "Community (Payment Needed)"
    assert summary["display_detail"] is None
    assert summary["tier_statuses"]["community"]["status"] == "approved_unpaid"


def test_requested_upgrade_is_distinct_from_active_access():
    summary = _summary(
        community_paid_until=FUTURE,
        requested_tiers=["club"],
    )

    assert summary["paid_tier"] == "community"
    assert summary["display_label"] == "Community Member"
    assert summary["display_detail"] == "Club request: Requested"
    assert summary["tier_statuses"]["community"]["status"] == "active"
    assert summary["tier_statuses"]["club"]["status"] == "requested"


def test_pending_payment_is_distinct_from_plain_request():
    summary = _summary(
        requested_tiers=["club"],
        pending_payment_reference="PAY-123",
        pending_tier_payments={"club": "PAY-123"},
    )

    assert summary["display_label"] == "Club (Payment Pending)"
    assert summary["payment_pending"] is True
    assert summary["tier_statuses"]["club"]["status"] == "payment_pending"


def test_paid_tier_keeps_pending_upgrade_as_secondary_display_detail():
    summary = _summary(
        community_paid_until=FUTURE,
        requested_tiers=["club"],
        pending_tier_payments={"club": "PAY-123"},
    )

    assert summary["display_label"] == "Community Member"
    assert summary["display_detail"] == "Club payment: Payment pending"


def test_pending_payment_only_marks_its_own_tier():
    summary = _summary(
        active_tiers=["community", "club"],
        requested_tiers=["club"],
        pending_payment_reference="PAY-123",
        pending_tier_payments={"club": "PAY-123"},
    )

    assert summary["tier_statuses"]["club"]["status"] == "payment_pending"
    assert summary["tier_statuses"]["community"]["status"] == "approved_unpaid"


def test_legacy_or_non_membership_reference_does_not_mark_a_tier_pending():
    summary = _summary(pending_payment_reference="SESSION-123")

    assert summary["payment_pending"] is False
    assert summary["tier_statuses"]["community"]["status"] == "approved_unpaid"


def test_paid_academy_is_an_independent_programme_status():
    summary = _summary(
        primary_tier="academy",
        active_tiers=["academy", "club", "community"],
        academy_paid_until=FUTURE,
    )

    assert summary["paid_tier"] == "academy"
    assert summary["paid_tiers"] == ["academy"]
    assert summary["display_label"] == "Academy Member"
    assert summary["tier_statuses"]["academy"]["direct_paid"] is True
    assert summary["tier_statuses"]["club"]["status"] == "inactive"
    assert summary["tier_statuses"]["community"]["status"] == "approved_unpaid"


def test_expired_declared_tier_is_not_active_but_remains_visible():
    summary = _summary(
        primary_tier="club",
        active_tiers=["club", "community"],
        club_paid_until=PAST,
    )

    assert summary["paid_tier"] == "prospect"
    assert summary["display_label"] == "Club (Expired)"
    assert summary["tier_statuses"]["club"]["status"] == "expired"
    assert summary["tier_statuses"]["club"]["declared_active"] is True


def test_post_academy_bridge_is_effective_club_with_auditable_source():
    summary = _summary(post_academy_club_until=FUTURE)

    assert summary["paid_tier"] == "club"
    assert summary["paid_tiers"] == ["club"]
    assert summary["tier_statuses"]["club"]["status"] == "active"
    assert summary["tier_statuses"]["club"]["direct_paid"] is False
    assert summary["tier_statuses"]["club"]["access_source"] == "post_academy"


def test_contract_separates_declared_identity_from_effective_paid_access():
    summary = _summary(
        primary_tier="prospect",
        active_tiers=[],
        declared_tiers=["community", "club", "academy"],
        academy_paid_until=PAST,
        community_paid_until=FUTURE,
    )

    assert summary["declared_tiers"] == ["academy", "club", "community"]
    assert summary["effective_paid_tiers"] == ["community"]
    assert summary["highest_paid_tier"] == "community"
    assert summary["paid_tiers"] == summary["effective_paid_tiers"]
    assert summary["paid_tier"] == summary["highest_paid_tier"]
