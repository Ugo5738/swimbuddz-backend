"""Admin store catalog router: categories, products, variants, images, collections."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.store_service.models import (
    AuditEntityType,
    Category,
    Collection,
    CollectionProduct,
    InventoryItem,
    Product,
    ProductImage,
    ProductStatus,
    ProductVariant,
)
from services.store_service.routers._helpers import log_audit
from services.store_service.schemas import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    CollectionCreate,
    CollectionResponse,
    CollectionUpdate,
    ProductCreate,
    ProductDetail,
    ProductImageCreate,
    ProductImageResponse,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
    ProductVariantCreate,
    ProductVariantResponse,
    ProductVariantUpdate,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(tags=["admin-store"])


# ============================================================================
# CATEGORIES
# ============================================================================


@router.get("/categories", response_model=list[CategoryResponse])
async def list_all_categories(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all categories (including inactive)."""
    query = select(Category).order_by(Category.sort_order, Category.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED
)
async def create_category(
    category_in: CategoryCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new category."""
    # Check slug uniqueness
    existing = await db.execute(
        select(Category).where(Category.slug == category_in.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Category with this slug already exists"
        )

    category = Category(**category_in.model_dump())
    db.add(category)
    await db.commit()
    await db.refresh(category)

    await log_audit(
        db,
        AuditEntityType.CATEGORY,
        category.id,
        "created",
        current_user.user_id,
        new_value=category_in.model_dump(),
    )
    await db.commit()

    return category


@router.patch("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: uuid.UUID,
    category_in: CategoryUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a category."""
    query = select(Category).where(Category.id == category_id)
    result = await db.execute(query)
    category = result.scalar_one_or_none()

    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    old_values = {
        "name": category.name,
        "slug": category.slug,
        "is_active": category.is_active,
    }

    update_data = category_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(category, field, value)

    await log_audit(
        db,
        AuditEntityType.CATEGORY,
        category.id,
        "updated",
        current_user.user_id,
        old_value=old_values,
        new_value=update_data,
    )

    await db.commit()
    await db.refresh(category)
    return category


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Archive a category (soft delete by setting is_active=False)."""
    query = select(Category).where(Category.id == category_id)
    result = await db.execute(query)
    category = result.scalar_one_or_none()

    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    category.is_active = False
    await log_audit(
        db, AuditEntityType.CATEGORY, category.id, "archived", current_user.user_id
    )
    await db.commit()
    return None


# ============================================================================
# PRODUCTS
# ============================================================================


@router.get("/products", response_model=ProductListResponse)
async def list_all_products(
    status_filter: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all products (including drafts)."""
    query = select(Product)

    if status_filter:
        query = query.where(Product.status == status_filter)

    if search:
        search_term = f"%{search}%"
        query = query.where(
            Product.name.ilike(search_term) | Product.sku.ilike(search_term)
        )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(Product.created_at.desc())
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


@router.post(
    "/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED
)
async def create_product(
    product_in: ProductCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new product."""
    # Check slug uniqueness
    existing = await db.execute(select(Product).where(Product.slug == product_in.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Product with this slug already exists"
        )

    product = Product(**product_in.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)

    await log_audit(
        db,
        AuditEntityType.PRODUCT,
        product.id,
        "created",
        current_user.user_id,
        new_value={"name": product.name, "slug": product.slug},
    )
    await db.commit()

    return product


@router.get("/products/{product_id}", response_model=ProductDetail)
async def get_product_admin(
    product_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get product detail (admin view, includes drafts)."""
    query = (
        select(Product)
        .where(Product.id == product_id)
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

    return product


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: uuid.UUID,
    product_in: ProductUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a product."""
    query = select(Product).where(Product.id == product_id)
    result = await db.execute(query)
    product = result.scalar_one_or_none()

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    old_price = float(product.base_price_ngn)
    update_data = product_in.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(product, field, value)

    # Log price change specifically
    if "base_price_ngn" in update_data:
        await log_audit(
            db,
            AuditEntityType.PRODUCT,
            product.id,
            "price_changed",
            current_user.user_id,
            old_value={"base_price_ngn": old_price},
            new_value={"base_price_ngn": float(update_data["base_price_ngn"])},
        )
    else:
        await log_audit(
            db,
            AuditEntityType.PRODUCT,
            product.id,
            "updated",
            current_user.user_id,
            new_value=update_data,
        )

    await db.commit()
    await db.refresh(product)
    return product


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_product(
    product_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Archive a product (soft delete)."""
    query = select(Product).where(Product.id == product_id)
    result = await db.execute(query)
    product = result.scalar_one_or_none()

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product.status = ProductStatus.ARCHIVED
    await log_audit(
        db, AuditEntityType.PRODUCT, product.id, "archived", current_user.user_id
    )
    await db.commit()
    return None


# ============================================================================
# PRODUCT VARIANTS
# ============================================================================


@router.post(
    "/products/{product_id}/variants",
    response_model=ProductVariantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_variant(
    product_id: uuid.UUID,
    variant_in: ProductVariantCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a variant to a product."""
    # Check product exists
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Check SKU uniqueness
    existing = await db.execute(
        select(ProductVariant).where(ProductVariant.sku == variant_in.sku)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Variant with this SKU already exists"
        )

    variant = ProductVariant(product_id=product_id, **variant_in.model_dump())
    db.add(variant)
    await db.flush()

    # Create inventory item
    inventory_item = InventoryItem(variant_id=variant.id)
    db.add(inventory_item)

    await db.commit()
    await db.refresh(variant)
    return variant


@router.patch(
    "/products/{product_id}/variants/{variant_id}",
    response_model=ProductVariantResponse,
)
async def update_variant(
    product_id: uuid.UUID,
    variant_id: uuid.UUID,
    variant_in: ProductVariantUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a product variant."""
    query = select(ProductVariant).where(
        ProductVariant.id == variant_id,
        ProductVariant.product_id == product_id,
    )
    result = await db.execute(query)
    variant = result.scalar_one_or_none()

    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")

    update_data = variant_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(variant, field, value)

    await db.commit()
    await db.refresh(variant)
    return variant


@router.delete(
    "/products/{product_id}/variants/{variant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_variant(
    product_id: uuid.UUID,
    variant_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Deactivate a variant (soft delete)."""
    query = select(ProductVariant).where(
        ProductVariant.id == variant_id,
        ProductVariant.product_id == product_id,
    )
    result = await db.execute(query)
    variant = result.scalar_one_or_none()

    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")

    variant.is_active = False
    await db.commit()
    return None


# ============================================================================
# PRODUCT IMAGES
# ============================================================================


@router.post(
    "/products/{product_id}/images",
    response_model=ProductImageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_product_image(
    product_id: uuid.UUID,
    image_in: ProductImageCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Add an image to a product."""
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    image = ProductImage(product_id=product_id, **image_in.model_dump())
    db.add(image)
    await db.commit()
    await db.refresh(image)
    return image


@router.delete(
    "/products/{product_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_product_image(
    product_id: uuid.UUID,
    image_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a product image."""
    query = select(ProductImage).where(
        ProductImage.id == image_id,
        ProductImage.product_id == product_id,
    )
    result = await db.execute(query)
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    await db.delete(image)
    await db.commit()
    return None


# ============================================================================
# COLLECTIONS
# ============================================================================


@router.get("/collections", response_model=list[CollectionResponse])
async def list_all_collections(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all collections."""
    query = select(Collection).order_by(Collection.sort_order, Collection.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/collections",
    response_model=CollectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_collection(
    collection_in: CollectionCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new collection."""
    collection = Collection(**collection_in.model_dump())
    db.add(collection)
    await db.commit()
    await db.refresh(collection)
    return collection


@router.patch("/collections/{collection_id}", response_model=CollectionResponse)
async def update_collection(
    collection_id: uuid.UUID,
    collection_in: CollectionUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a collection."""
    query = select(Collection).where(Collection.id == collection_id)
    result = await db.execute(query)
    collection = result.scalar_one_or_none()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    update_data = collection_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(collection, field, value)

    await db.commit()
    await db.refresh(collection)
    return collection


@router.post(
    "/collections/{collection_id}/products/{product_id}",
    status_code=status.HTTP_201_CREATED,
)
async def add_product_to_collection(
    collection_id: uuid.UUID,
    product_id: uuid.UUID,
    sort_order: int = 0,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a product to a collection."""
    # Verify both exist
    collection = await db.get(Collection, collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Check if already in collection
    existing = await db.execute(
        select(CollectionProduct).where(
            CollectionProduct.collection_id == collection_id,
            CollectionProduct.product_id == product_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Product already in collection")

    cp = CollectionProduct(
        collection_id=collection_id,
        product_id=product_id,
        sort_order=sort_order,
    )
    db.add(cp)
    await db.commit()

    return {"message": "Product added to collection"}


@router.delete(
    "/collections/{collection_id}/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_product_from_collection(
    collection_id: uuid.UUID,
    product_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove a product from a collection."""
    query = select(CollectionProduct).where(
        CollectionProduct.collection_id == collection_id,
        CollectionProduct.product_id == product_id,
    )
    result = await db.execute(query)
    cp = result.scalar_one_or_none()

    if not cp:
        raise HTTPException(status_code=404, detail="Product not in collection")

    await db.delete(cp)
    await db.commit()
    return None
