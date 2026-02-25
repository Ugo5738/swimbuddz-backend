"""Public store API router.

Endpoints for catalog browsing, cart operations, checkout, and order history.
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import get_current_user, get_optional_user
from libs.auth.models import AuthUser
from libs.common.currency import kobo_to_bubbles
from libs.common.media_utils import resolve_media_url, resolve_media_urls
from libs.common.service_client import check_wallet_balance, debit_member_wallet
from libs.db.session import get_async_db
from services.store_service.models import (
    Cart,
    CartItem,
    CartStatus,
    Category,
    Collection,
    CollectionProduct,
    FulfillmentType,
    InventoryMovement,
    InventoryMovementType,
    Order,
    OrderItem,
    OrderStatus,
    PickupLocation,
    Product,
    ProductStatus,
    ProductVariant,
    StoreCredit,
)
from services.store_service.schemas import (
    ApplyDiscountRequest,
    CartItemCreate,
    CartItemResponse,
    CartItemUpdate,
    CartResponse,
    CategoryResponse,
    CheckoutStartRequest,
    CheckoutStartResponse,
    CollectionResponse,
    CollectionWithProducts,
    MemberStoreCreditSummary,
    OrderResponse,
    PickupLocationResponse,
    ProductDetail,
    ProductListResponse,
    ProductResponse,
    ProductVariantWithInventory,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(tags=["store"])

# Constants
CART_EXPIRY_MINUTES = 30
DELIVERY_FEE_NGN = Decimal("2000")  # Flat delivery fee for now


# ============================================================================
# CATALOG - CATEGORIES
# ============================================================================


@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(
    db: AsyncSession = Depends(get_async_db),
):
    """List all active categories."""
    query = (
        select(Category)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.name)
    )
    result = await db.execute(query)
    categories = result.scalars().all()

    # Resolve image URLs
    media_ids = [c.image_media_id for c in categories if c.image_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    responses = []
    for cat in categories:
        resp = CategoryResponse.model_validate(cat).model_dump()
        if cat.image_media_id:
            resp["image_url"] = url_map.get(cat.image_media_id)
        responses.append(resp)
    return responses


@router.get("/categories/{slug}", response_model=CategoryResponse)
async def get_category(
    slug: str,
    db: AsyncSession = Depends(get_async_db),
):
    """Get category by slug."""
    query = select(Category).where(Category.slug == slug, Category.is_active.is_(True))
    result = await db.execute(query)
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


# ============================================================================
# CATALOG - COLLECTIONS
# ============================================================================


@router.get("/collections", response_model=list[CollectionResponse])
async def list_collections(
    db: AsyncSession = Depends(get_async_db),
):
    """List all active collections."""
    query = (
        select(Collection)
        .where(Collection.is_active.is_(True))
        .order_by(Collection.sort_order, Collection.name)
    )
    result = await db.execute(query)
    collections = result.scalars().all()

    # Resolve image URLs
    media_ids = [c.image_media_id for c in collections if c.image_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    responses = []
    for coll in collections:
        resp = CollectionResponse.model_validate(coll).model_dump()
        if coll.image_media_id:
            resp["image_url"] = url_map.get(coll.image_media_id)
        responses.append(resp)
    return responses


@router.get("/collections/{slug}", response_model=CollectionWithProducts)
async def get_collection(
    slug: str,
    db: AsyncSession = Depends(get_async_db),
):
    """Get collection by slug with products."""
    query = (
        select(Collection)
        .where(Collection.slug == slug, Collection.is_active.is_(True))
        .options(
            selectinload(Collection.collection_products).selectinload(
                CollectionProduct.product
            )
        )
    )
    result = await db.execute(query)
    collection = result.scalar_one_or_none()
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Extract products (only active ones)
    products = [
        cp.product
        for cp in sorted(collection.collection_products, key=lambda x: x.sort_order)
        if cp.product.status == ProductStatus.ACTIVE
    ]

    # Resolve image URL
    image_url = await resolve_media_url(collection.image_media_id)

    return CollectionWithProducts(
        id=collection.id,
        name=collection.name,
        slug=collection.slug,
        description=collection.description,
        image_url=image_url,
        image_media_id=collection.image_media_id,
        is_active=collection.is_active,
        sort_order=collection.sort_order,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
        products=[ProductResponse.model_validate(p) for p in products],
    )


# ============================================================================
# CATALOG - PRODUCTS
# ============================================================================


@router.get("/products", response_model=ProductListResponse)
async def list_products(
    category_slug: Optional[str] = None,
    search: Optional[str] = None,
    featured: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """Browse products with filtering and pagination."""
    query = select(Product).where(Product.status == ProductStatus.ACTIVE)

    # Category filter
    if category_slug:
        query = query.join(Category).where(Category.slug == category_slug)

    # Search filter
    if search:
        search_term = f"%{search}%"
        query = query.where(
            Product.name.ilike(search_term) | Product.description.ilike(search_term)
        )

    # Featured filter
    if featured is not None:
        query = query.where(Product.is_featured == featured)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Pagination
    query = query.order_by(Product.is_featured.desc(), Product.name)
    query = query.options(selectinload(Product.images))  # Load images for cards
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    products = result.scalars().all()

    return ProductListResponse(
        items=[ProductResponse.model_validate(p) for p in products],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


@router.get("/products/{slug}", response_model=ProductDetail)
async def get_product(
    slug: str,
    db: AsyncSession = Depends(get_async_db),
):
    """Get product detail with variants and images."""
    query = (
        select(Product)
        .where(Product.slug == slug, Product.status == ProductStatus.ACTIVE)
        .options(
            selectinload(Product.variants).selectinload(ProductVariant.inventory_item),
            selectinload(Product.images),
            selectinload(Product.category),
        )
    )
    result = await db.execute(query)
    product = result.scalar_one_or_none()

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Build variants with inventory
    variants_with_inventory = []
    for v in product.variants:
        if not v.is_active:
            continue
        inv = v.inventory_item
        variants_with_inventory.append(
            ProductVariantWithInventory(
                id=v.id,
                product_id=v.product_id,
                sku=v.sku,
                name=v.name,
                options=v.options,
                price_override_ngn=v.price_override_ngn,
                weight_grams=v.weight_grams,
                is_active=v.is_active,
                created_at=v.created_at,
                updated_at=v.updated_at,
                quantity_available=inv.quantity_available if inv else 0,
                quantity_on_hand=inv.quantity_on_hand if inv else 0,
            )
        )

    # Resolve size chart URL
    size_chart_url = await resolve_media_url(product.size_chart_media_id)

    return ProductDetail(
        id=product.id,
        name=product.name,
        slug=product.slug,
        category_id=product.category_id,
        description=product.description,
        short_description=product.short_description,
        base_price_ngn=product.base_price_ngn,
        compare_at_price_ngn=product.compare_at_price_ngn,
        status=product.status,
        is_featured=product.is_featured,
        meta_title=product.meta_title,
        meta_description=product.meta_description,
        has_variants=product.has_variants,
        variant_options=product.variant_options,
        sourcing_type=product.sourcing_type,
        preorder_lead_days=product.preorder_lead_days,
        requires_size_chart_ack=product.requires_size_chart_ack,
        size_chart_url=size_chart_url,
        size_chart_media_id=product.size_chart_media_id,
        created_at=product.created_at,
        updated_at=product.updated_at,
        variants=variants_with_inventory,
        images=[p for p in product.images],
        category=product.category,
    )


# ============================================================================
# PICKUP LOCATIONS
# ============================================================================


@router.get("/pickup-locations", response_model=list[PickupLocationResponse])
async def list_pickup_locations(
    db: AsyncSession = Depends(get_async_db),
):
    """List active pickup locations."""
    query = (
        select(PickupLocation)
        .where(PickupLocation.is_active.is_(True))
        .order_by(PickupLocation.sort_order, PickupLocation.name)
    )
    result = await db.execute(query)
    return result.scalars().all()


# ============================================================================
# CART
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


# ============================================================================
# CHECKOUT
# ============================================================================


@router.post("/checkout/start", response_model=CheckoutStartResponse)
async def start_checkout(
    request: CheckoutStartRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Start checkout process - validate cart, reserve inventory, create pending order."""
    # Get member's active cart
    query = (
        select(Cart)
        .where(
            Cart.member_auth_id == current_user.user_id,
            Cart.status == CartStatus.ACTIVE,
        )
        .options(
            selectinload(Cart.items)
            .selectinload(CartItem.variant)
            .selectinload(ProductVariant.product),
            selectinload(Cart.items)
            .selectinload(CartItem.variant)
            .selectinload(ProductVariant.inventory_item),
        )
    )
    result = await db.execute(query)
    cart = result.scalar_one_or_none()

    if not cart or not cart.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    # Validate fulfillment
    if request.fulfillment_type == FulfillmentType.PICKUP:
        if not request.pickup_location_id:
            raise HTTPException(status_code=400, detail="Pickup location required")
        # Validate pickup location exists
        loc_query = select(PickupLocation).where(
            PickupLocation.id == request.pickup_location_id,
            PickupLocation.is_active.is_(True),
        )
        loc_result = await db.execute(loc_query)
        if not loc_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Invalid pickup location")
    elif request.fulfillment_type == FulfillmentType.DELIVERY:
        if not request.delivery_address:
            raise HTTPException(status_code=400, detail="Delivery address required")

    # Check size chart acknowledgment if needed
    needs_size_ack = any(
        item.variant.product.requires_size_chart_ack for item in cart.items
    )
    if needs_size_ack and not request.size_chart_acknowledged:
        raise HTTPException(
            status_code=400,
            detail="Size chart acknowledgment required for swimwear products",
        )

    # Validate inventory and reserve
    for item in cart.items:
        inv = item.variant.inventory_item
        if not inv:
            raise HTTPException(
                status_code=400,
                detail=f"Inventory not available for {item.variant.sku}",
            )
        if inv.quantity_available < item.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Only {inv.quantity_available} available for {item.variant.sku}",
            )
        # Reserve inventory
        inv.quantity_reserved += item.quantity
        # Log movement
        movement = InventoryMovement(
            inventory_item_id=inv.id,
            movement_type=InventoryMovementType.RESERVATION,
            quantity=item.quantity,
            reference_type="cart",
            reference_id=cart.id,
        )
        db.add(movement)

    # Get member info for order
    member_row = await db.execute(
        text(
            "SELECT email, first_name, last_name, profile.phone FROM members "
            "LEFT JOIN member_profiles profile ON profile.member_id = members.id "
            "WHERE members.auth_id = :auth_id"
        ),
        {"auth_id": current_user.user_id},
    )
    member = member_row.mappings().first()
    if not member:
        raise HTTPException(status_code=400, detail="Member profile not found")

    # Calculate totals
    subtotal, discount_amount, total = await calculate_cart_totals(cart)

    # Add delivery fee if applicable
    delivery_fee = (
        DELIVERY_FEE_NGN
        if request.fulfillment_type == FulfillmentType.DELIVERY
        else Decimal("0")
    )
    final_total = total + delivery_fee

    # Apply store credits if requested
    store_credit_applied = Decimal("0")
    if request.apply_store_credit:
        # Get available store credits for member
        credits_query = (
            select(StoreCredit)
            .where(
                StoreCredit.member_auth_id == current_user.user_id,
                StoreCredit.balance_ngn > 0,
            )
            .order_by(StoreCredit.created_at)  # FIFO - oldest credits first
        )
        credits_result = await db.execute(credits_query)
        available_credits = list(credits_result.scalars().all())

        remaining_to_cover = final_total
        for credit in available_credits:
            if remaining_to_cover <= 0:
                break
            # Apply from this credit
            apply_amount = min(credit.balance_ngn, remaining_to_cover)
            credit.balance_ngn -= apply_amount
            store_credit_applied += apply_amount
            remaining_to_cover -= apply_amount

    # Amount remaining after store credit
    amount_after_credit = final_total - store_credit_applied

    # Bubbles wallet payment
    bubbles_applied: int | None = None
    wallet_txn_id: str | None = None
    order_status = OrderStatus.PENDING_PAYMENT

    if request.pay_with_bubbles and amount_after_credit > 0:
        bubbles_needed = kobo_to_bubbles(int(amount_after_credit * 100))
        if bubbles_needed > 0:
            # Check balance first (non-destructive)
            balance_check = await check_wallet_balance(
                current_user.user_id,
                required_amount=bubbles_needed,
                calling_service="store",
            )
            if not balance_check or not balance_check.get("sufficient"):
                current_balance = (
                    balance_check.get("current_balance", 0) if balance_check else 0
                )
                raise HTTPException(
                    status_code=402,
                    detail=f"Insufficient Bubbles. Need {bubbles_needed} 🫧, have {current_balance} 🫧.",
                )

    # Create order (flush to get ID before wallet debit for idempotency key)
    order = Order(
        order_number=Order.generate_order_number(),
        member_auth_id=current_user.user_id,
        customer_email=member["email"],
        customer_name=f"{member['first_name']} {member['last_name']}",
        customer_phone=member.get("phone")
        or (request.delivery_address.phone if request.delivery_address else None),
        subtotal_ngn=subtotal,
        discount_amount_ngn=discount_amount,
        store_credit_applied_ngn=store_credit_applied,
        delivery_fee_ngn=delivery_fee,
        total_ngn=amount_after_credit,
        discount_code=cart.discount_code,
        discount_breakdown={
            "member_tier_discount": float(cart.member_discount_percent or 0),
            "coupon_code": cart.discount_code,
        },
        status=OrderStatus.PENDING_PAYMENT,
        fulfillment_type=request.fulfillment_type,
        pickup_location_id=request.pickup_location_id,
        delivery_address=(
            request.delivery_address.model_dump() if request.delivery_address else None
        ),
        customer_notes=request.customer_notes,
    )
    db.add(order)
    await db.flush()  # Get order ID

    # Debit wallet after we have the order ID (use it as idempotency scope)
    if request.pay_with_bubbles and amount_after_credit > 0:
        bubbles_needed = kobo_to_bubbles(int(amount_after_credit * 100))
        if bubbles_needed > 0:
            try:
                result_txn = await debit_member_wallet(
                    current_user.user_id,
                    amount=bubbles_needed,
                    idempotency_key=f"order-{order.id}",
                    description=f"Store order {order.order_number} ({bubbles_needed} 🫧)",
                    calling_service="store",
                    transaction_type="purchase",
                    reference_type="order",
                    reference_id=str(order.id),
                )
                bubbles_applied = bubbles_needed
                wallet_txn_id = result_txn.get("transaction_id")
                order.bubbles_applied = bubbles_applied
                order.wallet_transaction_id = wallet_txn_id
                order.status = OrderStatus.PAID
                order.paid_at = datetime.utcnow()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400:
                    detail = e.response.json().get("detail", "")
                    if "Insufficient" in detail:
                        raise HTTPException(
                            status_code=402,
                            detail="Insufficient Bubbles. Please top up your wallet.",
                        )
                raise HTTPException(status_code=502, detail="Payment service error.")

    # Create order items
    for item in cart.items:
        variant = item.variant
        product = variant.product
        order_item = OrderItem(
            order_id=order.id,
            variant_id=variant.id,
            product_name=product.name,
            variant_name=variant.name,
            sku=variant.sku,
            quantity=item.quantity,
            unit_price_ngn=item.unit_price_ngn,
            line_total_ngn=item.unit_price_ngn * item.quantity,
            is_preorder=product.sourcing_type.value == "preorder",
            estimated_ship_date=(
                datetime.utcnow() + timedelta(days=product.preorder_lead_days or 0)
                if product.sourcing_type.value == "preorder"
                else None
            ),
        )
        db.add(order_item)

    # Mark cart as converted
    cart.status = CartStatus.CONVERTED
    await db.commit()

    return CheckoutStartResponse(
        order_id=order.id,
        order_number=order.order_number,
        total_ngn=order.total_ngn,
        delivery_fee_ngn=order.delivery_fee_ngn,
        requires_payment=(
            order.total_ngn > 0 and order.status == OrderStatus.PENDING_PAYMENT
        ),
        bubbles_applied=bubbles_applied,
    )


# ============================================================================
# ORDERS
# ============================================================================


@router.get("/orders", response_model=list[OrderResponse])
async def list_my_orders(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List member's orders."""
    query = (
        select(Order)
        .where(Order.member_auth_id == current_user.user_id)
        .options(selectinload(Order.items), selectinload(Order.pickup_location))
        .order_by(Order.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/orders/{order_number}", response_model=OrderResponse)
async def get_order(
    order_number: str,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get order by order number."""
    query = (
        select(Order)
        .where(
            Order.order_number == order_number,
            Order.member_auth_id == current_user.user_id,
        )
        .options(selectinload(Order.items), selectinload(Order.pickup_location))
    )
    result = await db.execute(query)
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


# ============================================================================
# STORE CREDITS
# ============================================================================


@router.get("/credits/me", response_model=MemberStoreCreditSummary)
async def get_my_store_credits(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get member's store credits."""
    query = (
        select(StoreCredit)
        .where(
            StoreCredit.member_auth_id == current_user.user_id,
            StoreCredit.balance_ngn > 0,
        )
        .order_by(StoreCredit.created_at.desc())
    )
    result = await db.execute(query)
    credits = result.scalars().all()

    total_balance = sum(c.balance_ngn for c in credits)

    return MemberStoreCreditSummary(
        total_balance_ngn=total_balance,
        credits=credits,
    )
