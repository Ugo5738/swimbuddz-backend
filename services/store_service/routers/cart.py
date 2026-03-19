"""Store cart router: cart operations and discount codes."""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import get_optional_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id, validate_discount_code
from libs.db.session import get_async_db
from services.store_service.models import (
    Cart,
    CartItem,
    CartStatus,
    Product,
    ProductStatus,
    ProductVariant,
    SourcingType,
)
from services.store_service.schemas import (
    ApplyDiscountRequest,
    CartItemCreate,
    CartItemResponse,
    CartItemUpdate,
    CartResponse,
)

router = APIRouter(tags=["store"])
logger = get_logger(__name__)

# Constants
CART_EXPIRY_MINUTES = 30

# Member tier discount mapping (membership_type → discount %)
TIER_DISCOUNT_MAP = {
    "club": Decimal("5"),  # Club members get 5% off store items
    "community": Decimal("0"),  # Community members: no tier discount
    "academy": Decimal("3"),  # Academy students get 3% off
}


# ============================================================================
# CART HELPERS
# ============================================================================


async def _apply_member_tier_discount(cart: Cart, auth_id: str) -> None:
    """Best-effort: look up member tier and apply store discount."""
    try:
        member = await get_member_by_auth_id(auth_id, calling_service="store")
        if member:
            membership_type = member.get("membership_type", "community")
            discount_pct = TIER_DISCOUNT_MAP.get(membership_type, Decimal("0"))
            cart.member_discount_percent = discount_pct
    except Exception:
        logger.warning("Could not fetch member tier for %s, skipping discount", auth_id)
        cart.member_discount_percent = Decimal("0")


async def get_or_create_cart(
    db: AsyncSession,
    user: Optional[AuthUser],
    session_id: Optional[str] = None,
) -> Cart:
    """Get existing active cart or create new one.

    If user is authenticated AND session_id is provided, merge any guest cart
    items into the member's cart. This preserves the shopping cart when a guest
    logs in.
    """
    # If authenticated, look for member cart
    if user:
        query = (
            select(Cart)
            .where(
                Cart.member_auth_id == user.user_id,
                Cart.status == CartStatus.ACTIVE,
            )
            .order_by(Cart.created_at.desc())
            .options(selectinload(Cart.items))
        )
        result = await db.execute(query)
        member_carts = result.scalars().all()
        member_cart = member_carts[0] if member_carts else None

        # Deactivate any duplicate active carts (keep only the newest)
        if len(member_carts) > 1:
            for stale_cart in member_carts[1:]:
                stale_cart.status = CartStatus.ABANDONED
            await db.flush()

        # Check for guest cart to merge (if session_id provided)
        guest_cart = None
        if session_id:
            guest_query = (
                select(Cart)
                .where(
                    Cart.session_id == session_id,
                    Cart.status == CartStatus.ACTIVE,
                    Cart.member_auth_id.is_(None),  # Must be a guest cart
                )
                .order_by(Cart.created_at.desc())
                .options(selectinload(Cart.items))
            )
            guest_result = await db.execute(guest_query)
            guest_cart = guest_result.scalars().first()

        # If we have a guest cart with items, merge into member cart
        if guest_cart and guest_cart.items:
            # Create member cart if needed
            member_cart_is_new = False
            if not member_cart:
                member_cart = Cart(
                    member_auth_id=user.user_id,
                    expires_at=datetime.utcnow()
                    + timedelta(minutes=CART_EXPIRY_MINUTES),
                )
                db.add(member_cart)
                await db.flush()
                member_cart_is_new = True

            # Merge items from guest cart into member cart.
            # For a newly created cart, items is uninitialised — avoid lazy-load
            # (triggers MissingGreenlet in async context).
            existing_variant_ids: set = (
                set()
                if member_cart_is_new
                else {item.variant_id for item in member_cart.items}
            )
            for guest_item in guest_cart.items:
                if guest_item.variant_id in existing_variant_ids:
                    # Update quantity if variant already in member cart
                    for member_item in member_cart.items:
                        if member_item.variant_id == guest_item.variant_id:
                            member_item.quantity += guest_item.quantity
                            break
                else:
                    # Copy item to member cart
                    new_item = CartItem(
                        cart_id=member_cart.id,
                        variant_id=guest_item.variant_id,
                        quantity=guest_item.quantity,
                        unit_price_ngn=guest_item.unit_price_ngn,
                    )
                    db.add(new_item)
                    existing_variant_ids.add(guest_item.variant_id)

            # Mark guest cart as consumed (prevent reuse)
            guest_cart.status = CartStatus.ABANDONED
            await db.commit()
            await db.refresh(member_cart)
            return member_cart

        # Return existing member cart or create new one
        if member_cart:
            # Auto-populate member tier discount if not set
            if member_cart.member_discount_percent is None:
                await _apply_member_tier_discount(member_cart, user.user_id)
                await db.commit()
                await db.refresh(member_cart)
            return member_cart

        # Create new cart for member
        cart = Cart(
            member_auth_id=user.user_id,
            expires_at=datetime.utcnow() + timedelta(minutes=CART_EXPIRY_MINUTES),
        )
        db.add(cart)
        await db.flush()

        # Auto-populate member tier discount
        await _apply_member_tier_discount(cart, user.user_id)

        await db.commit()
        await db.refresh(cart)
        return cart

    # Guest cart by session_id (not authenticated)
    if session_id:
        query = (
            select(Cart)
            .where(
                Cart.session_id == session_id,
                Cart.status == CartStatus.ACTIVE,
            )
            .order_by(Cart.created_at.desc())
        )
        result = await db.execute(query)
        cart = result.scalars().first()
        if cart:
            return cart

        # Create new guest cart
        cart = Cart(
            session_id=session_id,
            expires_at=datetime.utcnow() + timedelta(minutes=CART_EXPIRY_MINUTES),
        )
        db.add(cart)
        await db.commit()
        await db.refresh(cart)
        return cart

    raise HTTPException(status_code=400, detail="Session ID required for guest cart")


async def calculate_cart_totals(
    cart: Cart,
    coupon_discount_ngn: Decimal = Decimal("0"),
) -> tuple[Decimal, Decimal, Decimal]:
    """Calculate cart subtotal, discount, and total.

    Args:
        cart: The cart instance with items loaded.
        coupon_discount_ngn: Pre-calculated coupon discount in NGN (from payments_service).

    Returns:
        (subtotal, total_discount, final_total)
    """
    subtotal = Decimal("0")
    for item in cart.items:
        subtotal += item.unit_price_ngn * item.quantity

    discount_amount = Decimal("0")

    # Apply member tier discount
    if cart.member_discount_percent:
        discount_amount += subtotal * (cart.member_discount_percent / 100)

    # Apply coupon discount (validated externally and passed in)
    discount_amount += coupon_discount_ngn

    total = subtotal - discount_amount
    return subtotal, discount_amount, max(total, Decimal("0"))


async def _resolve_coupon_discount(cart: Cart, subtotal: Decimal) -> Decimal:
    """Best-effort resolve coupon discount amount from payments_service.

    Returns the NGN discount amount, or 0 if validation fails / no code.
    """
    if not cart.discount_code:
        return Decimal("0")
    try:
        result = await validate_discount_code(
            cart.discount_code,
            purpose="store_order",
            amount=float(subtotal),
            calling_service="store",
        )
        if result and result.get("valid"):
            return Decimal(str(result.get("discount_amount", 0)))
    except Exception:
        logger.warning("Failed to resolve coupon %s, skipping", cart.discount_code)
    return Decimal("0")


async def enrich_cart_response(cart_or_id, db: AsyncSession) -> CartResponse:
    """Enrich cart with item details and calculated totals.

    Accepts either a Cart object or a cart UUID. Using a UUID avoids
    MissingGreenlet errors from stale objects after commit/delete.
    """
    cart_id = cart_or_id if not isinstance(cart_or_id, Cart) else cart_or_id.id
    # Clear identity map to avoid stale lazy-load references after commit/delete
    db.expunge_all()
    # Load items with variants, product, and images (variant + product level)
    query = (
        select(Cart)
        .where(Cart.id == cart_id)
        .options(
            selectinload(Cart.items)
            .selectinload(CartItem.variant)
            .selectinload(ProductVariant.product)
            .selectinload(Product.images),
            selectinload(Cart.items)
            .selectinload(CartItem.variant)
            .selectinload(ProductVariant.images),
        )
    )
    result = await db.execute(query)
    cart = result.scalar_one()

    # Build enriched items
    enriched_items = []
    for item in cart.items:
        variant = item.variant
        product = variant.product if variant else None
        # Try variant images first, fall back to product images
        variant_imgs = variant.images or [] if variant else []
        product_imgs = product.images or [] if product else []
        all_imgs = variant_imgs or product_imgs
        primary_image = next(
            (img for img in all_imgs if img.is_primary),
            (all_imgs[0] if all_imgs else None),
        )

        enriched_items.append(
            CartItemResponse(
                id=item.id,
                variant_id=item.variant_id,
                quantity=item.quantity,
                unit_price_ngn=item.unit_price_ngn,
                product_name=product.name if product else None,
                variant_name=(
                    variant.name
                    if variant and variant.name and variant.name != "Default"
                    else None
                ),
                sku=variant.sku if variant else None,
                image_url=primary_image.url if primary_image else None,
            )
        )

    # Pre-calculate subtotal for coupon resolution
    raw_subtotal = sum(item.unit_price_ngn * item.quantity for item in cart.items)
    coupon_discount = await _resolve_coupon_discount(cart, raw_subtotal)
    subtotal, discount_amount, total = await calculate_cart_totals(
        cart, coupon_discount_ngn=coupon_discount
    )

    return CartResponse(
        id=cart.id,
        status=cart.status,
        discount_code=cart.discount_code,
        member_discount_percent=cart.member_discount_percent,
        expires_at=cart.expires_at,
        created_at=cart.created_at,
        updated_at=cart.updated_at,
        items=enriched_items,
        subtotal_ngn=subtotal,
        discount_amount_ngn=discount_amount,
        total_ngn=total,
    )


# ============================================================================
# CART ENDPOINTS
# ============================================================================


@router.get("/cart", response_model=CartResponse)
async def get_cart(
    session_id: Optional[str] = Query(None),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get current cart."""
    cart = await get_or_create_cart(db, current_user, session_id)
    return await enrich_cart_response(cart, db)


@router.post("/cart/items", response_model=CartResponse)
async def add_to_cart(
    item_in: CartItemCreate,
    session_id: Optional[str] = Query(None),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Add item to cart."""
    cart = await get_or_create_cart(db, current_user, session_id)

    # Get variant and check availability
    query = (
        select(ProductVariant)
        .where(
            ProductVariant.id == item_in.variant_id, ProductVariant.is_active.is_(True)
        )
        .options(
            selectinload(ProductVariant.product),
            selectinload(ProductVariant.inventory_item),
        )
    )
    result = await db.execute(query)
    variant = result.scalar_one_or_none()

    if not variant:
        raise HTTPException(status_code=404, detail="Product variant not found")

    if variant.product.status != ProductStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Product is not available")

    # Check inventory (skip for pre-order / dropship products)
    inv = variant.inventory_item
    if (
        inv
        and variant.product.sourcing_type
        not in (SourcingType.PREORDER, SourcingType.DROPSHIP)
        and inv.quantity_available < item_in.quantity
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Only {inv.quantity_available} available",
        )

    # Determine price
    unit_price = variant.price_override_ngn or variant.product.base_price_ngn

    # Check if item already in cart
    existing_query = select(CartItem).where(
        CartItem.cart_id == cart.id,
        CartItem.variant_id == item_in.variant_id,
    )
    existing_result = await db.execute(existing_query)
    existing_item = existing_result.scalar_one_or_none()

    if existing_item:
        new_quantity = existing_item.quantity + item_in.quantity
        if (
            inv
            and variant.product.sourcing_type
            not in (SourcingType.PREORDER, SourcingType.DROPSHIP)
            and inv.quantity_available < new_quantity
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot add more. Only {inv.quantity_available} available.",
            )
        existing_item.quantity = new_quantity
    else:
        cart_item = CartItem(
            cart_id=cart.id,
            variant_id=item_in.variant_id,
            quantity=item_in.quantity,
            unit_price_ngn=unit_price,
        )
        db.add(cart_item)

    # Update cart expiry
    cart.expires_at = datetime.utcnow() + timedelta(minutes=CART_EXPIRY_MINUTES)

    await db.commit()
    return await enrich_cart_response(cart, db)


@router.patch("/cart/items/{item_id}", response_model=CartResponse)
async def update_cart_item(
    item_id: uuid.UUID,
    item_in: CartItemUpdate,
    session_id: Optional[str] = Query(None),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update cart item quantity."""
    cart = await get_or_create_cart(db, current_user, session_id)

    # Get cart item
    query = (
        select(CartItem)
        .where(CartItem.id == item_id, CartItem.cart_id == cart.id)
        .options(
            selectinload(CartItem.variant).selectinload(ProductVariant.inventory_item),
            selectinload(CartItem.variant).selectinload(ProductVariant.product),
        )
    )
    result = await db.execute(query)
    cart_item = result.scalar_one_or_none()

    if not cart_item:
        raise HTTPException(status_code=404, detail="Cart item not found")

    # Check inventory (skip for pre-order / dropship products)
    inv = cart_item.variant.inventory_item if cart_item.variant else None
    product = cart_item.variant.product if cart_item.variant else None
    if (
        inv
        and product
        and product.sourcing_type not in (SourcingType.PREORDER, SourcingType.DROPSHIP)
        and inv.quantity_available < item_in.quantity
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Only {inv.quantity_available} available",
        )

    cart_item.quantity = item_in.quantity
    await db.commit()
    return await enrich_cart_response(cart.id, db)


@router.delete("/cart/items/{item_id}", response_model=CartResponse)
async def remove_cart_item(
    item_id: uuid.UUID,
    session_id: Optional[str] = Query(None),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove item from cart."""
    cart = await get_or_create_cart(db, current_user, session_id)

    # Get cart item
    query = select(CartItem).where(CartItem.id == item_id, CartItem.cart_id == cart.id)
    result = await db.execute(query)
    cart_item = result.scalar_one_or_none()

    if not cart_item:
        raise HTTPException(status_code=404, detail="Cart item not found")

    cart_id = cart.id  # Save before delete invalidates references
    await db.delete(cart_item)
    await db.commit()
    return await enrich_cart_response(cart_id, db)


@router.post("/cart/discount", response_model=CartResponse)
async def apply_discount_code(
    request: ApplyDiscountRequest,
    session_id: Optional[str] = Query(None),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply and validate discount code on cart."""
    cart = await get_or_create_cart(db, current_user, session_id)

    # Validate discount code via payments_service
    code = request.code.upper().strip()
    try:
        result = await validate_discount_code(
            code,
            purpose="store_order",
            amount=0,  # Validation only — amount applied at total calculation
            member_auth_id=(current_user.user_id if current_user else None),
            calling_service="store",
        )
    except Exception:
        logger.warning("Payments service unreachable for discount validation")
        raise HTTPException(
            status_code=502,
            detail="Unable to validate discount code. Please try again.",
        )

    if not result or not result.get("valid"):
        raise HTTPException(
            status_code=400,
            detail=result.get("message", "Invalid discount code")
            if result
            else "Invalid discount code",
        )

    cart.discount_code = code
    await db.commit()
    return await enrich_cart_response(cart, db)


@router.delete("/cart/discount", response_model=CartResponse)
async def remove_discount_code(
    session_id: Optional[str] = Query(None),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove discount code from cart."""
    cart = await get_or_create_cart(db, current_user, session_id)
    cart.discount_code = None
    await db.commit()
    return await enrich_cart_response(cart, db)
