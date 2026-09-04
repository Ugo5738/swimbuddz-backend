from datetime import date, datetime, timezone

from services.members_service.services.membership_pricing import (
    annual_membership_extension,
)


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
ANNUAL_FEE = 2_000_000


def test_existing_membership_that_covers_period_adds_nothing():
    assert annual_membership_extension(
        paid_until=datetime(2027, 12, 31, tzinfo=timezone.utc),
        coverage_end=date(2027, 12, 31),
        annual_fee_kobo=ANNUAL_FEE,
        now=NOW,
    ) == (0, 0)


def test_missing_membership_adds_one_annual_block_for_near_period():
    assert annual_membership_extension(
        paid_until=None,
        coverage_end=date(2027, 6, 30),
        annual_fee_kobo=ANNUAL_FEE,
        now=NOW,
    ) == (12, ANNUAL_FEE)


def test_future_selection_adds_enough_whole_annual_blocks():
    assert annual_membership_extension(
        paid_until=None,
        coverage_end=date(2028, 12, 31),
        annual_fee_kobo=ANNUAL_FEE,
        now=NOW,
    ) == (36, 3 * ANNUAL_FEE)


def test_unexpired_membership_extension_stacks_from_paid_until():
    assert annual_membership_extension(
        paid_until=datetime(2027, 3, 31, tzinfo=timezone.utc),
        coverage_end=date(2028, 3, 31),
        annual_fee_kobo=ANNUAL_FEE,
        now=NOW,
    ) == (12, ANNUAL_FEE)
