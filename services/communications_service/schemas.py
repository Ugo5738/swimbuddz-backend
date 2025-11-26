import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from services.communications_service.models import AnnouncementCategory


class AnnouncementBase(BaseModel):
    title: str
    summary: Optional[str] = None
    body: str
    category: AnnouncementCategory = AnnouncementCategory.GENERAL
    is_pinned: bool = False
    published_at: datetime


class AnnouncementCreate(AnnouncementBase):
    pass


class AnnouncementResponse(AnnouncementBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== CONTENT SCHEMAS =====
class ContentPostBase(BaseModel):
    """Base schema for content posts."""
    title: str
    summary: Optional[str] = None
    body: str  # Markdown content
    category: str  # swimming_tips/safety/breathing/technique/news/education
    featured_image_url: Optional[str] = None
    tier_access: str = "community"  # community/club/academy


class ContentPostCreate(ContentPostBase):
    """Schema for creating a content post."""
    is_published: bool = False


class ContentPostUpdate(BaseModel):
    """Schema for updating a content post."""
    title: Optional[str] = None
    summary: Optional[str] = None
    body: Optional[str] = None
    category: Optional[str] = None
    featured_image_url: Optional[str] = None
    tier_access: Optional[str] = None
    is_published: Optional[bool] = None


class ContentPostResponse(ContentPostBase):
    """Content post response schema."""
    id: uuid.UUID
    is_published: bool
    published_at: Optional[datetime] = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    comment_count: Optional[int] = 0

    model_config = ConfigDict(from_attributes=True)


# ===== COMMENT SCHEMAS =====
class CommentCreate(BaseModel):
    """Schema for creating a comment."""
    content: str


class ContentCommentResponse(BaseModel):
    """Content comment response schema."""
    id: uuid.UUID
    post_id: uuid.UUID
    member_id: uuid.UUID
    content: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnnouncementCommentResponse(BaseModel):
    """Announcement comment response schema."""
    id: uuid.UUID
    announcement_id: uuid.UUID
    member_id: uuid.UUID
    content: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
