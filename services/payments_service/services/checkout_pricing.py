"""Shared, server-owned product checkout arithmetic (all money in kobo).

Discounts reduce eligible products; Bubbles settle the discounted price. Provider
charges apply only to the remaining cash. Original commercial snapshots stay intact.
"""

from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import select

from libs.common.currency import KOBO_PER_BUBBLE, naira_to_kobo
from libs.common.datetime_utils import utc_now
from services.payments_service.models import Discount, DiscountType, PaymentPurpose
from services.payments_service.services.additional_charges import (
    calculate_additional_charges,
)


PRODUCT_PURPOSES = {
    PaymentPurpose.COMMUNITY,
    PaymentPurpose.CLUB,
    PaymentPurpose.CLUB_BUNDLE,
    PaymentPurpose.ACADEMY_COHORT,
    PaymentPurpose.COMMUNITY_EXPERIENCE,
}
BUNDLED_EXPERIENCE = "community_experience_bundle"


def verify_expected_total(quote, expected_total_kobo):
    if expected_total_kobo is not None and quote["total_kobo"] != expected_total_kobo:
        raise HTTPException(
            409,
            "Checkout price changed. Refresh and review the new total before paying.",
        )


def product_components(purpose, subtotal_kobo: int, metadata: dict) -> dict[str, int]:
    """Only call with locally resolved or service-authenticated commercial data."""
    raw = metadata.get("components_kobo") or {}
    if raw:
        experience_key = (
            BUNDLED_EXPERIENCE
            if (metadata.get("community_experience_option") or {}).get("price_context")
            == "club_bundle"
            else "community_experience"
        )
        values = {
            "club": int(raw.get("club") or 0),
            "community": int(raw.get("annual_swimbuddz_membership") or 0),
            "academy_cohort": int(raw.get("academy") or 0),
            experience_key: int(raw.get("community_experience") or 0),
        }
    elif metadata.get("checkout_components_kobo") is not None:
        # Named Experience orders own their ticket and Membership snapshots.
        values = dict(metadata["checkout_components_kobo"])
    elif metadata.get("components") and purpose == PaymentPurpose.CLUB_BUNDLE:
        values = {
            key: naira_to_kobo(value) for key, value in metadata["components"].items()
        }
    elif purpose == PaymentPurpose.CLUB and metadata.get("community_extension_amount"):
        community = naira_to_kobo(metadata["community_extension_amount"])
        values = {"community": community, "club": subtotal_kobo - community}
    else:
        values = {purpose.value: subtotal_kobo}
    if (
        any(not isinstance(value, int) or value < 0 for value in values.values())
        or sum(values.values()) != subtotal_kobo
    ):
        raise HTTPException(409, "Checkout components do not match the quoted amount")
    return values


def discount_allocation(discount, components: dict[str, int]) -> dict[str, int]:
    scopes = {scope.lower() for scope in (discount.applies_to or [])}
    # Historical 'Club + Membership' promotions still target those two products.
    if "club_bundle" in scopes:
        scopes |= {"club", "community"}
    eligible = {
        key: amount
        for key, amount in components.items()
        if amount > 0 and (key in scopes or (not scopes and key != BUNDLED_EXPERIENCE))
    }
    total = sum(eligible.values())
    if total == 0:
        raise HTTPException(
            400,
            "This code does not apply to the selected items. Bundled Experience discounts need explicit Admin approval.",
        )
    if discount.discount_type == DiscountType.PERCENTAGE:
        reduction = int(
            (Decimal(total) * Decimal(str(discount.value)) / 100).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    else:
        reduction = naira_to_kobo(discount.value)
    reduction = min(total, max(0, reduction))
    # Largest-remainder allocation keeps fixed discounts exact and repeatable.
    allocated = {key: reduction * value // total for key, value in eligible.items()}
    remainder = reduction - sum(allocated.values())
    order = sorted(
        eligible, key=lambda key: (-(reduction * eligible[key] % total), key)
    )
    for key in order[:remainder]:
        allocated[key] += 1
    return allocated


async def price_product_checkout(
    db,
    *,
    purpose,
    currency,
    components: dict[str, int],
    payment_method="paystack",
    discount_code: str | None = None,
    bubbles_to_apply: int = 0,
    consume_discount: bool = False,
) -> dict:
    if currency != "NGN" and (discount_code or bubbles_to_apply):
        raise HTTPException(400, "Discounts and Bubbles currently require NGN checkout")
    allocations = {}
    code = None
    if discount_code:
        code = discount_code.strip().upper()
        query = select(Discount).where(
            Discount.code == code, Discount.is_active.is_(True)
        )
        if consume_discount:
            query = query.with_for_update()
        discount = (await db.execute(query)).scalar_one_or_none()
        now = utc_now()
        if not discount:
            raise HTTPException(400, "Invalid discount code")
        if discount.valid_from and discount.valid_from > now:
            raise HTTPException(400, "Discount code is not yet active")
        if discount.valid_until and discount.valid_until < now:
            raise HTTPException(400, "Discount code has expired")
        if discount.max_uses is not None and discount.current_uses >= discount.max_uses:
            raise HTTPException(400, "Discount code has reached its usage limit")
        allocations = discount_allocation(discount, components)
        if consume_discount:
            discount.current_uses += 1
            db.add(discount)
    subtotal = sum(components.values())
    discount_kobo = sum(allocations.values())
    net = subtotal - discount_kobo
    maximum = net // KOBO_PER_BUBBLE if currency == "NGN" else 0
    if bubbles_to_apply < 0 or bubbles_to_apply > maximum:
        raise HTTPException(
            400, f"At most {maximum} whole Bubbles can be applied to this checkout"
        )
    if bubbles_to_apply and payment_method != "paystack":
        raise HTTPException(400, "Bubbles can only be combined with online payment")
    cash = net - bubbles_to_apply * KOBO_PER_BUBBLE
    # A free or fully wallet-funded checkout must not acquire a fixed provider fee.
    lines, fees = (
        await calculate_additional_charges(
            db,
            purpose=purpose,
            payment_method=payment_method,
            subtotal_kobo=cash,
        )
        if cash > 0
        else ([], 0)
    )
    return {
        "subtotal_kobo": subtotal,
        "discount_code": code,
        "discount_kobo": discount_kobo,
        "discount_allocations_kobo": allocations,
        "net_components_kobo": {
            key: amount - allocations.get(key, 0) for key, amount in components.items()
        },
        "net_subtotal_kobo": net,
        "bubbles_to_apply": bubbles_to_apply,
        "bubbles_value_kobo": bubbles_to_apply * KOBO_PER_BUBBLE,
        "maximum_bubbles": maximum,
        "cash_subtotal_kobo": cash,
        "additional_charges": lines,
        "additional_charges_total_kobo": fees,
        "total_kobo": cash + fees,
    }
