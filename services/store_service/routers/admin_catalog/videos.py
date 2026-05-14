"""Admin product-video endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.store_service.models import Product, ProductVideo
from services.store_service.schemas import ProductVideoCreate, ProductVideoResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["admin-store"])


@router.post(
    "/products/{product_id}/videos",
    response_model=ProductVideoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_product_video(
    product_id: uuid.UUID,
    video_in: ProductVideoCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a video to a product."""
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    video = ProductVideo(product_id=product_id, **video_in.model_dump())
    db.add(video)
    await db.commit()
    await db.refresh(video)
    return video


@router.delete(
    "/products/{product_id}/videos/{video_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_product_video(
    product_id: uuid.UUID,
    video_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a product video."""
    query = select(ProductVideo).where(
        ProductVideo.id == video_id,
        ProductVideo.product_id == product_id,
    )
    result = await db.execute(query)
    video = result.scalar_one_or_none()

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    await db.delete(video)
    await db.commit()
    return None
