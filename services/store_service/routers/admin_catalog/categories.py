"""Admin category endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.store_service.models import AuditEntityType, Category
from services.store_service.routers._helpers import log_audit
from services.store_service.schemas import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


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

    update_data = category_in.model_dump(exclude_unset=True)

    # Check slug uniqueness (exclude this category)
    new_slug = update_data.get("slug")
    if new_slug and new_slug != category.slug:
        existing = await db.execute(
            select(Category).where(
                Category.slug == new_slug, Category.id != category_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Another category with this slug already exists",
            )

    old_values = {
        "name": category.name,
        "slug": category.slug,
        "is_active": category.is_active,
    }

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
