"""Unit tests for corporate wellness pricing rules.

Source of truth: docs/marketing/CORPORATE_WELLNESS.md (Part 1 pricing table).
"""

import pytest

from services.corporate_service.models.enums import DiscountTier
from services.corporate_service.services.pricing import (
    PRICE_BULK_10_PLUS_KOBO,
    PRICE_BULK_5_9_KOBO,
    PRICE_FULL_KOBO,
    compute_program_pricing,
    discount_tier_for_count,
)


class TestPriceConstants:
    """Hard-code the kobo values so a typo in the source costs us nothing
    accidentally — these prices are the contract with HR buyers."""

    def test_full_price_is_150k_naira(self):
        assert PRICE_FULL_KOBO == 15_000_000  # ₦150,000

    def test_bulk_5_9_is_135k_naira(self):
        assert PRICE_BULK_5_9_KOBO == 13_500_000  # ₦135,000

    def test_bulk_10_plus_is_127_5k_naira(self):
        assert PRICE_BULK_10_PLUS_KOBO == 12_750_000  # ₦127,500


class TestDiscountTierForCount:
    @pytest.mark.parametrize("count", [1, 2, 3, 4])
    def test_under_5_employees_gets_full_price(self, count):
        assert discount_tier_for_count(count) == DiscountTier.FULL_PRICE

    @pytest.mark.parametrize("count", [5, 6, 7, 8, 9])
    def test_5_to_9_employees_gets_bulk_tier(self, count):
        assert discount_tier_for_count(count) == DiscountTier.BULK_5_9

    @pytest.mark.parametrize("count", [10, 15, 50, 100])
    def test_10_plus_employees_gets_max_discount(self, count):
        assert discount_tier_for_count(count) == DiscountTier.BULK_10_PLUS


class TestComputeProgramPricing:
    def test_4_employees_full_price(self):
        per, total = compute_program_pricing(4, DiscountTier.FULL_PRICE)
        assert per == PRICE_FULL_KOBO
        assert total == 4 * PRICE_FULL_KOBO  # ₦600,000

    def test_8_employees_bulk_tier(self):
        per, total = compute_program_pricing(8, DiscountTier.BULK_5_9)
        assert per == PRICE_BULK_5_9_KOBO
        assert total == 8 * PRICE_BULK_5_9_KOBO  # ₦1,080,000

    def test_15_employees_max_discount(self):
        per, total = compute_program_pricing(15, DiscountTier.BULK_10_PLUS)
        assert per == PRICE_BULK_10_PLUS_KOBO
        assert total == 15 * PRICE_BULK_10_PLUS_KOBO  # ₦1,912,500

    def test_admin_can_override_tier_for_pilot_goodwill(self):
        """A 4-employee pilot can still be priced at the 5-9 tier if admin
        chooses — the helper just respects the tier passed in. The playbook
        documents the FLOOR of ₦127.5k below which coach economics break,
        but that's enforced by admin judgment, not this function."""
        per, total = compute_program_pricing(4, DiscountTier.BULK_5_9)
        assert per == PRICE_BULK_5_9_KOBO
        assert total == 4 * PRICE_BULK_5_9_KOBO
