"""Academy, Club, and annual Membership are independent products."""

from datetime import timedelta

from libs.common.datetime_utils import utc_now
from services.members_service.services.member_service import normalize_member_tiers


def test_open_academy_access_does_not_imply_membership_or_club():
    future = utc_now() + timedelta(days=60)

    primary, products, _ = normalize_member_tiers(
        current_tier="prospect",
        current_tiers=[],
        community_paid_until=None,
        club_paid_until=None,
        academy_paid_until=future,
    )

    assert primary == "academy"
    assert products == ["academy"]


def test_independently_paid_products_can_coexist_without_inheritance():
    future = utc_now() + timedelta(days=60)

    primary, products, _ = normalize_member_tiers(
        current_tier="community",
        current_tiers=["community"],
        community_paid_until=future,
        club_paid_until=None,
        academy_paid_until=future,
    )

    assert primary == "academy"
    assert products == ["academy", "community"]
