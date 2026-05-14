"""Admin product endpoints."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.store_service.models import (
    AuditEntityType,
    Product,
    ProductStatus,
    ProductVariant,
)
from services.store_service.routers._helpers import log_audit
from services.store_service.schemas import (
    ProductCreate,
    ProductDetail,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(tags=["admin-store"])


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
        query = query.where(Product.name.ilike(search_term))

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate & eager-load relationships needed by ProductResponse
    query = query.options(
        selectinload(Product.images),
        selectinload(Product.category),
    )
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
            selectinload(Product.videos),
            selectinload(Product.category),
        )
    )
    result = await db.execute(query)
    product = result.scalar_one_or_none()

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return product


@router.patch("/products/{product_id}", response_model=ProductDetail)
async def update_product(
    product_id: uuid.UUID,
    product_in: ProductUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a product. Returns full nested detail."""
    query = select(Product).where(Product.id == product_id)
    result = await db.execute(query)
    product = result.scalar_one_or_none()

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    update_data = product_in.model_dump(exclude_unset=True)

    # Check slug uniqueness (exclude this product)
    new_slug = update_data.get("slug")
    if new_slug and new_slug != product.slug:
        existing = await db.execute(
            select(Product).where(Product.slug == new_slug, Product.id != product_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Another product with this slug already exists",
            )

    old_price = float(product.base_price_ngn)

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

    # Re-fetch with eager loading for full nested response
    detail_query = (
        select(Product)
        .where(Product.id == product_id)
        .options(
            selectinload(Product.variants).selectinload(ProductVariant.inventory_item),
            selectinload(Product.images),
            selectinload(Product.videos),
            selectinload(Product.category),
        )
    )
    detail_result = await db.execute(detail_query)
    product = detail_result.scalar_one()
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
