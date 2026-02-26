"""Communications announcement categories router."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.communications_service.models import AnnouncementCategoryConfig
from services.communications_service.schemas import (
    AnnouncementCategoryConfigCreate,
    AnnouncementCategoryConfigResponse,
    AnnouncementCategoryConfigUpdate,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

category_router = APIRouter(prefix="/categories", tags=["announcement-categories"])


@category_router.get("/", response_model=List[AnnouncementCategoryConfigResponse])
async def list_announcement_categories(
    include_inactive: bool = Query(False, description="Include inactive categories"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all custom announcement categories.
    """
    query = select(AnnouncementCategoryConfig)
    if not include_inactive:
        query = query.where(AnnouncementCategoryConfig.is_active.is_(True))
    query = query.order_by(AnnouncementCategoryConfig.display_name)

    result = await db.execute(query)
    return result.scalars().all()


@category_router.post(
    "/", response_model=AnnouncementCategoryConfigResponse, status_code=201
)
async def create_announcement_category(
    category_data: AnnouncementCategoryConfigCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a custom announcement category (Admin only).
    """
    # Check for duplicate name
    existing_query = select(AnnouncementCategoryConfig).where(
        AnnouncementCategoryConfig.name == category_data.name.lower().replace(" ", "_")
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Category with this name already exists"
        )

    category = AnnouncementCategoryConfig(
        name=category_data.name.lower().replace(" ", "_"),
        display_name=category_data.display_name,
        description=category_data.description,
        auto_expire_hours=category_data.auto_expire_hours,
        default_notify_email=category_data.default_notify_email,
        default_notify_push=category_data.default_notify_push,
        icon=category_data.icon,
        color=category_data.color,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


@category_router.patch(
    "/{category_id}", response_model=AnnouncementCategoryConfigResponse
)
async def update_announcement_category(
    category_id: uuid.UUID,
    category_data: AnnouncementCategoryConfigUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update a custom announcement category (Admin only).
    """
    query = select(AnnouncementCategoryConfig).where(
        AnnouncementCategoryConfig.id == category_id
    )
    result = await db.execute(query)
    category = result.scalar_one_or_none()

    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    update_data = category_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(category, field, value)

    await db.commit()
    await db.refresh(category)
    return category


@category_router.delete("/{category_id}", status_code=204)
async def delete_announcement_category(
    category_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete a custom announcement category (Admin only).
    Note: This will not delete announcements using this category.
    """
    query = select(AnnouncementCategoryConfig).where(
        AnnouncementCategoryConfig.id == category_id
    )
    result = await db.execute(query)
    category = result.scalar_one_or_none()

    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    await db.delete(category)
    await db.commit()
