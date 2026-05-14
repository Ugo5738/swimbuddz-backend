"""Category schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CategoryBase(BaseModel):
    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=100)
    description: Optional[str] = None
    image_media_id: Optional[uuid.UUID] = None
    parent_id: Optional[uuid.UUID] = None
    sort_order: int = 0
    is_active: bool = True


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    slug: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    image_media_id: Optional[uuid.UUID] = None
    parent_id: Optional[uuid.UUID] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class CategoryResponse(CategoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    image_url: Optional[str] = None  # Resolved from media_id
    created_at: datetime
    updated_at: datetime


class CategoryWithChildren(CategoryResponse):
    children: list["CategoryWithChildren"] = []
