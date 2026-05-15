"""Admin product-image endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.store_service.models import Product, ProductImage, ProductVariant
from services.store_service.schemas import (
    ProductImageCreate,
    ProductImageResponse,
    ProductImageUpdate,
)
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


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


@router.patch(
    "/products/{product_id}/images/{image_id}",
    response_model=ProductImageResponse,
)
async def update_product_image(
    product_id: uuid.UUID,
    image_id: uuid.UUID,
    image_in: ProductImageUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a product image (variant linking, sort order, primary, alt text)."""
    query = select(ProductImage).where(
        ProductImage.id == image_id,
        ProductImage.product_id == product_id,
    )
    result = await db.execute(query)
    image = result.scalar_one_or_none()

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    update_data = image_in.model_dump(exclude_unset=True)

    # Validate variant_id belongs to this product
    new_variant_id = update_data.get("variant_id")
    if new_variant_id is not None:
        variant = await db.execute(
            select(ProductVariant).where(
                ProductVariant.id == new_variant_id,
                ProductVariant.product_id == product_id,
            )
        )
        if not variant.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Variant not found for this product",
            )

    # If setting as primary, unset the old primary first
    if update_data.get("is_primary"):
        await db.execute(
            sa_update(ProductImage)
            .where(
                ProductImage.product_id == product_id,
                ProductImage.is_primary == True,  # noqa: E712
                ProductImage.id != image_id,
            )
            .values(is_primary=False)
        )

    for field, value in update_data.items():
        setattr(image, field, value)

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
