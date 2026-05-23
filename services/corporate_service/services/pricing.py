"""Corporate wellness pricing rules — source: docs/marketing/CORPORATE_WELLNESS.md.

Pricing in kobo (1 NGN = 100 kobo). DO NOT use floats; integer kobo only.
"""

from services.corporate_service.models.enums import DiscountTier

# Per-employee tiers (kobo)
PRICE_FULL_KOBO = 15_000_000  # ₦150,000 — 1-4 employees
PRICE_BULK_5_9_KOBO = 13_500_000  # ₦135,000 — 5-9 employees (10% off)
PRICE_BULK_10_PLUS_KOBO = 12_750_000  # ₦127,500 — 10+ employees (15% off)

_TIER_PRICES = {
    DiscountTier.FULL_PRICE: PRICE_FULL_KOBO,
    DiscountTier.BULK_5_9: PRICE_BULK_5_9_KOBO,
    DiscountTier.BULK_10_PLUS: PRICE_BULK_10_PLUS_KOBO,
}


def discount_tier_for_count(employee_count: int) -> DiscountTier:
    """Return the discount tier that applies to a given headcount."""
    if employee_count >= 10:
        return DiscountTier.BULK_10_PLUS
    if employee_count >= 5:
        return DiscountTier.BULK_5_9
    return DiscountTier.FULL_PRICE


def compute_program_pricing(employee_count: int, tier: DiscountTier) -> tuple[int, int]:
    """Return ``(per_employee_kobo, total_kobo)`` for a program.

    The caller picks the tier (so admins can override — e.g. extending a 5-tier
    rate to a 4-employee pilot as a goodwill gesture); this helper just looks
    up the price and multiplies. The playbook also documents a floor of
    ₦127.5k below which coach economics break — admins can still set custom
    prices on the program record after creation if needed.
    """
    per = _TIER_PRICES[tier]
    return per, per * employee_count
