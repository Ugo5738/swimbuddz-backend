"""Discount code lookup, expiry/usage validation, and the math that
turns a discount + a payment purpose into a post-discount amount.

Used only by `intent_creation.create_payment_intent`.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.logging import get_logger
from services.payments_service.models import (
    Discount,
    DiscountType,
    PaymentPurpose,
)

settings = get_settings()
logger = get_logger(__name__)

FULFILLMENT_META_KEY = "fulfillment"
MAX_FULFILLMENT_RETRIES = 8
BASE_FULFILLMENT_RETRY_MINUTES = 2


async def _validate_and_apply_discount(
    db: AsyncSession,
    discount_code: str | None,
    purpose: PaymentPurpose,
    original_amount: float,
    member_auth_id: str,
    components: (
        dict[str, float] | None
    ) = None,  # e.g., {"community": 20000, "club": 150000}
) -> tuple[float, float | None, Discount | None, str | None]:
    """
    Validate and apply a discount code if provided.
    Returns: (final_amount, discount_applied, discount_obj, applies_to_component)

    Smart Component Matching:
    - If payment is CLUB_BUNDLE and discount only applies to COMMUNITY,
      discount is calculated on the COMMUNITY portion only.
    """
    if not discount_code:
        return original_amount, None, None, None

    from libs.common.datetime_utils import utc_now

    # Lookup discount code
    query = select(Discount).where(
        Discount.code == discount_code.upper().strip(),
        Discount.is_active.is_(True),
    )
    result = await db.execute(query)
    discount = result.scalar_one_or_none()

    if not discount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid discount code: {discount_code}",
        )

    now = utc_now()

    # Check validity period
    if discount.valid_from and discount.valid_from > now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discount code is not yet active",
        )
    if discount.valid_until and discount.valid_until < now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discount code has expired",
        )

    # Check usage limits
    if discount.max_uses and discount.current_uses >= discount.max_uses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discount code has reached its usage limit",
        )

    # Smart Component Matching
    applicable_purposes = [p.upper() for p in (discount.applies_to or [])]
    purpose_upper = purpose.value.upper()

    # Determine what amount the discount applies to
    applicable_amount = original_amount
    applies_to_component = None

    if applicable_purposes:
        # Direct match - discount applies to the exact purpose
        if purpose_upper in applicable_purposes:
            applicable_amount = original_amount
            applies_to_component = purpose_upper.lower()

        # Smart component matching for bundles
        elif purpose_upper == "CLUB_BUNDLE" and components:
            # Check if discount applies to COMMUNITY portion
            if "COMMUNITY" in applicable_purposes and "community" in components:
                applicable_amount = components["community"]
                applies_to_component = "community"
            # Check if discount applies to CLUB portion
            elif "CLUB" in applicable_purposes and "club" in components:
                applicable_amount = components["club"]
                applies_to_component = "club"
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Discount code does not apply to any component in this payment",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Discount code does not apply to {purpose.value} payments",
            )

    # Calculate discount amount based on applicable amount
    if discount.discount_type == DiscountType.PERCENTAGE:
        discount_amount = applicable_amount * (discount.value / 100)
    else:  # FIXED
        discount_amount = min(discount.value, applicable_amount)

    # Ensure discount doesn't exceed applicable amount
    discount_amount = min(discount_amount, applicable_amount)
    final_amount = max(original_amount - discount_amount, 0)

    # Increment usage count
    discount.current_uses += 1
    db.add(discount)

    return final_amount, discount_amount, discount, applies_to_component
