"""Pydantic schemas for Media Service."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


# ===== ALBUM SCHEMAS =====
class AlbumBase(BaseModel):
    """Base album schema."""

    title: str
    description: Optional[str] = None
    album_type: (
        str  # GENERAL, SESSION, EVENT, ACADEMY, PRODUCT, MARKETING, USER_GENERATED
    )
    linked_entity_id: Optional[uuid.UUID] = None
    linked_entity_type: Optional[str] = None
    owner_entity_id: Optional[uuid.UUID] = None
    is_public: bool = True
    slug: Optional[str] = None


class AlbumCreate(AlbumBase):
    """Schema for creating an album."""

    pass


class AlbumUpdate(BaseModel):
    """Schema for updating an album."""

    title: Optional[str] = None
    description: Optional[str] = None
    album_type: Optional[str] = None
    linked_entity_id: Optional[uuid.UUID] = None
    linked_entity_type: Optional[str] = None
    cover_media_id: Optional[uuid.UUID] = None
    is_public: Optional[bool] = None
    slug: Optional[str] = None


class AlbumResponse(AlbumBase):
    """Album response schema."""

    id: uuid.UUID
    cover_media_id: Optional[uuid.UUID] = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    media_count: Optional[int] = 0

    model_config = ConfigDict(from_attributes=True)


# ===== MEDIA ITEM SCHEMAS =====
class MediaItemBase(BaseModel):
    """Base media item schema."""

    title: Optional[str] = None
    description: Optional[str] = None
    alt_text: Optional[str] = None
    media_type: str = "IMAGE"  # IMAGE, VIDEO, DOCUMENT


class MediaItemUpload(MediaItemBase):
    """Schema for media upload (file handled separately)."""

    album_id: Optional[uuid.UUID] = None  # Optional, can upload without album initially


class MediaItemUpdate(BaseModel):
    """Schema for updating media metadata."""

    title: Optional[str] = None
    description: Optional[str] = None
    alt_text: Optional[str] = None
    is_processed: Optional[bool] = None


class MediaItemResponse(MediaItemBase):
    """Media item response schema."""

    id: uuid.UUID
    file_url: str
    thumbnail_url: Optional[str] = None
    metadata_info: Optional[Dict[str, Any]] = None
    is_processed: bool
    uploaded_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    tags: Optional[List[uuid.UUID]] = []  # List of tagged member IDs

    model_config = ConfigDict(from_attributes=True)


# ===== SITE ASSET SCHEMAS =====
class SiteAssetBase(BaseModel):
    """Base site asset schema."""

    key: str
    description: Optional[str] = None
    is_active: bool = True


class SiteAssetCreate(SiteAssetBase):
    """Schema for creating a site asset."""

    media_item_id: uuid.UUID


class SiteAssetUpdate(BaseModel):
    """Schema for updating a site asset."""

    description: Optional[str] = None
    is_active: Optional[bool] = None
    media_item_id: Optional[uuid.UUID] = None


class SiteAssetResponse(SiteAssetBase):
    """Site asset response schema."""

    id: uuid.UUID
    media_item_id: uuid.UUID
    media_item: Optional[MediaItemResponse] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== TAG SCHEMAS =====
class MediaTagCreate(BaseModel):
    """Schema for creating a media tag."""

    media_item_id: uuid.UUID
    member_id: uuid.UUID
    x_coord: Optional[float] = None
    y_coord: Optional[float] = None


class MediaTagResponse(BaseModel):
    """Media tag response schema."""

    id: uuid.UUID
    media_item_id: uuid.UUID
    member_id: uuid.UUID
    x_coord: Optional[float] = None
    y_coord: Optional[float] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== GALLERY VIEW SCHEMAS =====
class AlbumWithMedia(AlbumResponse):
    """Album with media items list."""

    media_items: List[MediaItemResponse] = []
