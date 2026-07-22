"""Unit tests for Academy lower-tier entitlement policy.

Academy extends Community for one year and inherits Club while Academy is
active. It does not create direct Club time; graduation grants the bridge.
"""

from datetime import timezone

from dateutil.relativedelta import relativedelta

from libs.common.datetime_utils import utc_now
from services.members_service.services.member_service import academy_bundle_expiry

NOW = utc_now().replace(microsecond=0)


def test_grants_one_year_community_without_direct_club_when_unset():
    community, club = academy_bundle_expiry(NOW, None, None)
    assert community == NOW + relativedelta(years=1)
    assert club is None


def test_extends_community_but_preserves_existing_club_date():
    expired_community = NOW - relativedelta(days=10)
    shorter_club = NOW + relativedelta(days=15)  # < 3-month floor
    community, club = academy_bundle_expiry(NOW, expired_community, shorter_club)
    assert community == NOW + relativedelta(years=1)
    assert club == shorter_club


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
    assert club is None
    # NOW is UTC, so the Community floor is too.
    assert community.utcoffset() == timezone.utc.utcoffset(None)
