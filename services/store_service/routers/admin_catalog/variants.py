"""Admin product-variant endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.store_service.models import InventoryItem, Product, ProductVariant
from services.store_service.schemas import (
    ProductVariantCreate,
    ProductVariantResponse,
    ProductVariantUpdate,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ._constants import CATEGORY_SKU_CODES

router = APIRouter(tags=["admin-store"])


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
    """Add a variant to a product. SKU is auto-generated if not provided."""
    # Check product exists (eager-load category for SKU generation)
    result = await db.execute(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.category))
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Auto-generate SKU if not provided
    sku = variant_in.sku
    if not sku:
        # Use category-based prefix (matching seed convention)
        cat_code = None
        if product.category:
            cat_code = CATEGORY_SKU_CODES.get(product.category.slug)
        if not cat_code:
            # Fallback to slug-based for uncategorized products
            slug_parts = product.slug.split("-")
            cat_code = "".join(p[:3].upper() for p in slug_parts[:2])
        prefix = f"SB-{cat_code}"

        # Count existing variants to determine the next sequence number
        count_result = await db.execute(
            select(func.count()).where(ProductVariant.product_id == product_id)
        )
        next_num = (count_result.scalar() or 0) + 1

        # Append variant name/size suffix if present
        suffix = ""
        if variant_in.name:
            # Use first meaningful part: "S (35-36)" → "S", "Default" → "DEF"
            name_part = variant_in.name.split()[0].upper()[:3]
            suffix = f"-{name_part}"

        sku = f"{prefix}-{next_num:03d}{suffix}"

        # Ensure uniqueness by appending sequence if collision
        existing = await db.execute(
            select(ProductVariant).where(ProductVariant.sku == sku)
        )
        while existing.scalar_one_or_none():
            next_num += 1
            sku = f"{prefix}-{next_num:03d}{suffix}"
            existing = await db.execute(
                select(ProductVariant).where(ProductVariant.sku == sku)
            )
    else:
        # Check SKU uniqueness for user-provided SKU
        existing = await db.execute(
            select(ProductVariant).where(ProductVariant.sku == sku)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=400, detail="Variant with this SKU already exists"
            )

    variant_data = variant_in.model_dump(exclude={"sku"})
    variant = ProductVariant(product_id=product_id, sku=sku, **variant_data)
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
