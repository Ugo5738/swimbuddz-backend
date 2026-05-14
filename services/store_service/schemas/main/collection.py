"""Collection schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .product import ProductResponse


class CollectionBase(BaseModel):
    name: str = Field(..., max_length=100)
    slug: str = Field(..., max_length=100)
    description: Optional[str] = None
    image_media_id: Optional[uuid.UUID] = None
    is_active: bool = True
    sort_order: int = 0


class CollectionCreate(CollectionBase):
    pass


class CollectionUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    slug: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    image_media_id: Optional[uuid.UUID] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class CollectionResponse(CollectionBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    image_url: Optional[str] = None  # Resolved from media_id
    created_at: datetime
    updated_at: datetime


class CollectionWithProducts(CollectionResponse):
    products: list[ProductResponse] = []
