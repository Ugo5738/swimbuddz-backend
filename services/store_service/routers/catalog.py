"""Store catalog router: categories, collections, products, pickup locations."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.common.media_utils import resolve_media_url, resolve_media_urls
from libs.db.session import get_async_db
from services.store_service.models import (
    Category,
    Collection,
    CollectionProduct,
    PickupLocation,
    Product,
    ProductStatus,
    ProductVariant,
)
from services.store_service.schemas import (
    CategoryResponse,
    CollectionResponse,
    CollectionWithProducts,
    PickupLocationResponse,
    ProductDetail,
    ProductListResponse,
    ProductResponse,
    ProductVariantWithInventory,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(tags=["store"])


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
