"""Store cart router: cart operations and discount codes."""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import get_optional_user
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.store_service.models import (
    Cart,
    CartItem,
    CartStatus,
    ProductStatus,
    ProductVariant,
)
from services.store_service.schemas import (
    ApplyDiscountRequest,
    CartItemCreate,
    CartItemResponse,
    CartItemUpdate,
    CartResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(tags=["store"])

# Constants
CART_EXPIRY_MINUTES = 30


# ============================================================================
# CART HELPERS
# ============================================================================


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
        member_cart = result.scalars().first()

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
            if not member_cart:
                member_cart = Cart(
                    member_auth_id=user.user_id,
                    expires_at=datetime.utcnow()
                    + timedelta(minutes=CART_EXPIRY_MINUTES),
                )
                db.add(member_cart)
                await db.flush()

            # Merge items from guest cart
            existing_variant_ids = {item.variant_id for item in member_cart.items}
            for guest_item in guest_cart.items:
                if guest_item.variant_id in existing_variant_ids:
                    # Update quantity if item already in member cart
                    for member_item in member_cart.items:
                        if member_item.variant_id == guest_item.variant_id:
                            member_item.quantity += guest_item.quantity
                            break
                else:
                    # Transfer item to member cart
                    new_item = CartItem(
                        cart_id=member_cart.id,
                        variant_id=guest_item.variant_id,
                        quantity=guest_item.quantity,
                        unit_price_ngn=guest_item.unit_price_ngn,
                    )
                    db.add(new_item)
                    existing_variant_ids.add(guest_item.variant_id)

            # Mark guest cart as merged (change status to prevent reuse)
            guest_cart.status = CartStatus.ABANDONED
            await db.commit()
            await db.refresh(member_cart)
            return member_cart

        # Return existing member cart or create new one
        if member_cart:
            return member_cart

        # Create new cart for member
        cart = Cart(
            member_auth_id=user.user_id,
            expires_at=datetime.utcnow() + timedelta(minutes=CART_EXPIRY_MINUTES),
        )
        db.add(cart)
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


async def calculate_cart_totals(cart: Cart) -> tuple[Decimal, Decimal, Decimal]:
    """Calculate cart subtotal, discount, and total."""
    subtotal = Decimal("0")
    for item in cart.items:
        subtotal += item.unit_price_ngn * item.quantity

    # Apply member discount
    discount_amount = Decimal("0")
    if cart.member_discount_percent:
        discount_amount = subtotal * (cart.member_discount_percent / 100)

    # TODO: Apply coupon discount

    total = subtotal - discount_amount
    return subtotal, discount_amount, max(total, Decimal("0"))


async def enrich_cart_response(cart: Cart, db: AsyncSession) -> CartResponse:
    """Enrich cart with item details and calculated totals."""
    # Load items with variants
    query = (
        select(Cart)
        .where(Cart.id == cart.id)
        .options(
            selectinload(Cart.items)
            .selectinload(CartItem.variant)
            .selectinload(ProductVariant.product),
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
        primary_image = next(
            (img for img in (variant.images or []) if img.is_primary),
            (variant.images[0] if variant.images else None),
        )

        enriched_items.append(
            CartItemResponse(
                id=item.id,
                variant_id=item.variant_id,
                quantity=item.quantity,
                unit_price_ngn=item.unit_price_ngn,
                product_name=product.name if product else None,
                variant_name=variant.name if variant else None,
                sku=variant.sku if variant else None,
                image_url=primary_image.url if primary_image else None,
            )
        )

    subtotal, discount_amount, total = await calculate_cart_totals(cart)

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

    # Check inventory
    inv = variant.inventory_item
    if inv and inv.quantity_available < item_in.quantity:
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
        if inv and inv.quantity_available < new_quantity:
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
            selectinload(CartItem.variant).selectinload(ProductVariant.inventory_item)
        )
    )
    result = await db.execute(query)
    cart_item = result.scalar_one_or_none()

    if not cart_item:
        raise HTTPException(status_code=404, detail="Cart item not found")

    # Check inventory
    inv = cart_item.variant.inventory_item if cart_item.variant else None
    if inv and inv.quantity_available < item_in.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Only {inv.quantity_available} available",
        )

    cart_item.quantity = item_in.quantity
    await db.commit()
    return await enrich_cart_response(cart, db)


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

    await db.delete(cart_item)
    await db.commit()
    return await enrich_cart_response(cart, db)


@router.post("/cart/discount", response_model=CartResponse)
async def apply_discount_code(
    request: ApplyDiscountRequest,
    session_id: Optional[str] = Query(None),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply discount code to cart."""
    cart = await get_or_create_cart(db, current_user, session_id)

    # TODO: Validate discount code from payments_service discounts table
    # For now, just store the code
    cart.discount_code = request.code.upper()
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
