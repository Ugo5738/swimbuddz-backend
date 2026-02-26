"""Discount CRUD (admin) and discount preview endpoint."""

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from pydantic import BaseModel
from services.payments_service.models import Discount, DiscountType
from services.payments_service.schemas import (
    DiscountCreate,
    DiscountResponse,
    DiscountUpdate,
)
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/payments", tags=["payments"])
settings = get_settings()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Inline schemas used only by the preview endpoint
# ---------------------------------------------------------------------------


class DiscountPreviewRequest(BaseModel):
    code: str
    purpose: str  # e.g., "club", "community", "club_bundle", "academy_cohort"
    subtotal: float  # The pre-discount total amount
    # Component breakdown for smart discount matching (optional)
    components: dict[str, float] | None = (
        None  # e.g., {"community": 20000, "club": 150000}
    )


class DiscountPreviewResponse(BaseModel):
    valid: bool
    code: str
    discount_type: str | None = None  # "PERCENTAGE" or "FIXED"
    discount_value: float | None = None  # e.g., 75 for 75% or 5000 for ₦5000
    discount_amount: float = 0  # The actual amount to be deducted
    final_total: float  # The total after discount
    applies_to_component: str | None = None  # Which component the discount applies to
    message: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/discounts/preview", response_model=DiscountPreviewResponse)
async def preview_discount(
    payload: DiscountPreviewRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Preview a discount code without creating a payment.
    Returns the calculated discount amount for display before checkout.
    Does NOT increment usage count.

    Smart Component Matching:
    - If discount applies to COMMUNITY and payment is CLUB_BUNDLE,
      discount only applies to the COMMUNITY portion.
    """
    from libs.common.datetime_utils import utc_now

    code = payload.code.upper().strip()

    # Lookup discount code
    query = select(Discount).where(
        Discount.code == code,
        Discount.is_active.is_(True),
    )
    result = await db.execute(query)
    discount = result.scalar_one_or_none()

    if not discount:
        return DiscountPreviewResponse(
            valid=False,
            code=code,
            final_total=payload.subtotal,
            message="Invalid discount code",
        )

    now = utc_now()

    # Check validity period
    if discount.valid_from and discount.valid_from > now:
        return DiscountPreviewResponse(
            valid=False,
            code=code,
            final_total=payload.subtotal,
            message="Discount code is not yet active",
        )

    if discount.valid_until and discount.valid_until < now:
        return DiscountPreviewResponse(
            valid=False,
            code=code,
            final_total=payload.subtotal,
            message="Discount code has expired",
        )

    # Check usage limits
    if discount.max_uses and discount.current_uses >= discount.max_uses:
        return DiscountPreviewResponse(
            valid=False,
            code=code,
            final_total=payload.subtotal,
            message="Discount code has reached its usage limit",
        )

    # Smart Component Matching
    # For bundle payments, check if discount applies to any individual component
    applicable_purposes = [p.upper() for p in (discount.applies_to or [])]
    purpose_upper = payload.purpose.upper()

    # Determine what amount the discount applies to
    applicable_amount = payload.subtotal
    applies_to_component = None

    if applicable_purposes:
        # Direct match - discount applies to the exact purpose
        if purpose_upper in applicable_purposes:
            applicable_amount = payload.subtotal
            applies_to_component = purpose_upper.lower()

        # Smart component matching for bundles
        elif purpose_upper == "CLUB_BUNDLE" and payload.components:
            # Check if discount applies to COMMUNITY portion
            if "COMMUNITY" in applicable_purposes and "community" in payload.components:
                applicable_amount = payload.components["community"]
                applies_to_component = "community"
            # Check if discount applies to CLUB portion
            elif "CLUB" in applicable_purposes and "club" in payload.components:
                applicable_amount = payload.components["club"]
                applies_to_component = "club"
            else:
                # Discount doesn't apply to any component in the bundle
                return DiscountPreviewResponse(
                    valid=False,
                    code=code,
                    final_total=payload.subtotal,
                    message="Discount code does not apply to any component in this payment",
                )
        else:
            # Discount doesn't apply to this purpose
            return DiscountPreviewResponse(
                valid=False,
                code=code,
                final_total=payload.subtotal,
                message=f"Discount code does not apply to {payload.purpose} payments",
            )

    # Calculate discount amount based on applicable amount
    if discount.discount_type == DiscountType.PERCENTAGE:
        discount_amount = applicable_amount * (discount.value / 100)
    else:  # FIXED
        discount_amount = min(discount.value, applicable_amount)

    # Ensure discount doesn't exceed applicable amount
    discount_amount = min(discount_amount, applicable_amount)
    final_total = max(payload.subtotal - discount_amount, 0)

    # Build message
    if applies_to_component:
        component_label = applies_to_component.replace("_", " ").title()
        message = f"{discount.value}{'%' if discount.discount_type == DiscountType.PERCENTAGE else ' NGN'} discount applied to {component_label}"
    else:
        message = f"{discount.value}{'%' if discount.discount_type == DiscountType.PERCENTAGE else ' NGN'} discount applied"

    return DiscountPreviewResponse(
        valid=True,
        code=discount.code,
        discount_type=discount.discount_type.value,
        discount_value=discount.value,
        discount_amount=discount_amount,
        final_total=final_total,
        applies_to_component=applies_to_component,
        message=message,
    )


@router.post("/admin/discounts", response_model=DiscountResponse)
async def create_discount(
    payload: DiscountCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new discount code (Admin only)."""
    from services.payments_service.models import DiscountType as DT

    # Check if code already exists
    existing = await db.execute(
        select(Discount).where(Discount.code == payload.code.upper().strip())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Discount code '{payload.code}' already exists",
        )

    discount = Discount(
        code=payload.code.upper().strip(),
        description=payload.description,
        discount_type=DT(payload.discount_type),
        value=payload.value,
        applies_to=payload.applies_to,
        valid_from=payload.valid_from,
        valid_until=payload.valid_until,
        max_uses=payload.max_uses,
        max_uses_per_user=payload.max_uses_per_user,
        is_active=payload.is_active,
    )
    db.add(discount)
    await db.commit()
    await db.refresh(discount)
    return discount


@router.get("/admin/discounts", response_model=list[DiscountResponse])
async def list_discounts(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all discount codes (Admin only)."""
    result = await db.execute(select(Discount).order_by(desc(Discount.created_at)))
    return result.scalars().all()


@router.get("/admin/discounts/{discount_id}", response_model=DiscountResponse)
async def get_discount(
    discount_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a specific discount code (Admin only)."""
    import uuid as uuid_mod

    try:
        uid = uuid_mod.UUID(discount_id)
        result = await db.execute(select(Discount).where(Discount.id == uid))
    except ValueError:
        # Try by code
        result = await db.execute(
            select(Discount).where(Discount.code == discount_id.upper().strip())
        )

    discount = result.scalar_one_or_none()
    if not discount:
        raise HTTPException(status_code=404, detail="Discount not found")
    return discount


@router.patch("/admin/discounts/{discount_id}", response_model=DiscountResponse)
async def update_discount(
    discount_id: str,
    payload: DiscountUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a discount code (Admin only)."""
    import uuid as uuid_mod

    from services.payments_service.models import DiscountType as DT

    try:
        uid = uuid_mod.UUID(discount_id)
        result = await db.execute(select(Discount).where(Discount.id == uid))
    except ValueError:
        result = await db.execute(
            select(Discount).where(Discount.code == discount_id.upper().strip())
        )

    discount = result.scalar_one_or_none()
    if not discount:
        raise HTTPException(status_code=404, detail="Discount not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "discount_type" in update_data and update_data["discount_type"]:
        update_data["discount_type"] = DT(update_data["discount_type"])

    for field, value in update_data.items():
        setattr(discount, field, value)

    await db.commit()
    await db.refresh(discount)
    return discount


@router.delete("/admin/discounts/{discount_id}")
async def delete_discount(
    discount_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a discount code (Admin only)."""
    import uuid as uuid_mod

    try:
        uid = uuid_mod.UUID(discount_id)
        result = await db.execute(select(Discount).where(Discount.id == uid))
    except ValueError:
        result = await db.execute(
            select(Discount).where(Discount.code == discount_id.upper().strip())
        )

    discount = result.scalar_one_or_none()
    if not discount:
        raise HTTPException(status_code=404, detail="Discount not found")

    await db.delete(discount)
    await db.commit()
    return {"deleted": True}
