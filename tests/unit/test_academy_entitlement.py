"""Unit tests for academy_bundle_expiry.

Academy enrollment bundles Community (1 year) + Club (3 months) access,
whether paid in full or by installment. These verify the pure date logic the
``academy/activate`` endpoint uses so paid academy members are never wrongly
shown the "Activate Community/Club" upsell.
"""

from datetime import timezone

from dateutil.relativedelta import relativedelta

from libs.common.datetime_utils import utc_now
from services.members_service.services.member_service import academy_bundle_expiry

NOW = utc_now().replace(microsecond=0)


def test_grants_one_year_community_and_three_months_club_when_unset():
    community, club = academy_bundle_expiry(NOW, None, None)
    assert community == NOW + relativedelta(years=1)
    assert club == NOW + relativedelta(months=3)


def test_extends_expired_or_shorter_dates_up_to_the_floor():
    expired_community = NOW - relativedelta(days=10)
    shorter_club = NOW + relativedelta(days=15)  # < 3-month floor
    community, club = academy_bundle_expiry(NOW, expired_community, shorter_club)
    assert community == NOW + relativedelta(years=1)
    assert club == NOW + relativedelta(months=3)


def test_never_shortens_a_longer_existing_entitlement():
    longer_community = NOW + relativedelta(years=3)
    longer_club = NOW + relativedelta(months=9)
    community, club = academy_bundle_expiry(NOW, longer_community, longer_club)
    assert community == longer_community
    assert club == longer_club


def test_idempotent_across_repeated_installment_payments():
    community1, club1 = academy_bundle_expiry(NOW, None, None)
    community2, club2 = academy_bundle_expiry(NOW, community1, club1)
    assert (community1, club1) == (community2, club2)


def test_returns_timezone_aware_datetimes():
    community, club = academy_bundle_expiry(NOW, None, None)
    assert community.tzinfo is not None
    assert club.tzinfo is not None
    # NOW is UTC, so the floors are too.
    assert community.utcoffset() == timezone.utc.utcoffset(None)
