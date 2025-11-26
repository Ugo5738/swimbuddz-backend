"""Pydantic schemas for Media Service."""
import uuid
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, ConfigDict


# ===== ALBUM SCHEMAS =====
class AlbumBase(BaseModel):
    """Base album schema."""
    title: str
    description: Optional[str] = None
    album_type: str  # session/event/academy/general
    linked_entity_id: Optional[uuid.UUID] = None


class AlbumCreate(AlbumBase):
    """Schema for creating an album."""
    pass


class AlbumUpdate(BaseModel):
    """Schema for updating an album."""
    title: Optional[str] = None
    description: Optional[str] = None
    album_type: Optional[str] = None
    linked_entity_id: Optional[uuid.UUID] = None
    cover_photo_id: Optional[uuid.UUID] = None


class AlbumResponse(AlbumBase):
    """Album response schema."""
    id: uuid.UUID
    cover_photo_id: Optional[uuid.UUID] = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    photo_count: Optional[int] = 0

    model_config = ConfigDict(from_attributes=True)


# ===== PHOTO SCHEMAS =====
class PhotoBase(BaseModel):
    """Base photo schema."""
    caption: Optional[str] = None
    taken_at: Optional[datetime] = None


class PhotoUpload(PhotoBase):
    """Schema for photo upload (file handled separately)."""
    album_id: uuid.UUID


class PhotoUpdate(BaseModel):
    """Schema for updating photo metadata."""
    caption: Optional[str] = None
    taken_at: Optional[datetime] = None
    is_featured: Optional[bool] = None


class PhotoResponse(PhotoBase):
    """Photo response schema."""
    id: uuid.UUID
    album_id: uuid.UUID
    file_url: str
    thumbnail_url: Optional[str] = None
    uploaded_by: uuid.UUID
    is_featured: bool
    created_at: datetime
    updated_at: datetime
    tags: Optional[List[uuid.UUID]] = []  # List of tagged member IDs

    model_config = ConfigDict(from_attributes=True)


# ===== TAG SCHEMAS =====
class PhotoTagCreate(BaseModel):
    """Schema for creating a photo tag."""
    photo_id: uuid.UUID
    member_id: uuid.UUID


class PhotoTagResponse(BaseModel):
    """Photo tag response schema."""
    id: uuid.UUID
    photo_id: uuid.UUID
    member_id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== GALLERY VIEW SCHEMAS =====
class AlbumWithPhotos(AlbumResponse):
    """Album with photos list."""
    photos: List[PhotoResponse] = []


class FeaturedPhotosResponse(BaseModel):
    """Response for featured photos (homepage)."""
    photos: List[PhotoResponse] = []
