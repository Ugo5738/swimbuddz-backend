import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict
from services.communications_service.models import (
    AnnouncementAudience,
    AnnouncementCategory,
    AnnouncementStatus,
)


class AnnouncementBase(BaseModel):
    title: str
    summary: Optional[str] = None
    body: str
    category: AnnouncementCategory = AnnouncementCategory.GENERAL
    custom_category: Optional[str] = None  # For category=CUSTOM
    status: AnnouncementStatus = AnnouncementStatus.PUBLISHED
    audience: AnnouncementAudience = AnnouncementAudience.COMMUNITY
    expires_at: Optional[datetime] = None
    notify_email: bool = True
    notify_push: bool = True
    is_pinned: bool = False
    scheduled_for: Optional[datetime] = None  # Schedule for future publishing
    published_at: Optional[datetime] = None


class AnnouncementCreate(AnnouncementBase):
    pass


class AnnouncementUpdate(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    body: Optional[str] = None
    category: Optional[AnnouncementCategory] = None
    custom_category: Optional[str] = None
    status: Optional[AnnouncementStatus] = None
    audience: Optional[AnnouncementAudience] = None
    expires_at: Optional[datetime] = None
    notify_email: Optional[bool] = None
    notify_push: Optional[bool] = None
    is_pinned: Optional[bool] = None
    scheduled_for: Optional[datetime] = None
    published_at: Optional[datetime] = None


class AnnouncementResponse(AnnouncementBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    read_count: Optional[int] = 0  # Number of members who have read this
    acknowledged_count: Optional[int] = 0  # Number of members who acknowledged

    model_config = ConfigDict(from_attributes=True)


# ===== ANNOUNCEMENT READ TRACKING =====
class AnnouncementReadCreate(BaseModel):
    """Schema for marking an announcement as read."""

    acknowledged: bool = False


class AnnouncementReadResponse(BaseModel):
    """Response showing read status for an announcement."""

    announcement_id: uuid.UUID
    member_id: uuid.UUID
    read_at: datetime
    acknowledged: bool
    acknowledged_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ===== CUSTOM CATEGORY CONFIGURATION =====
class AnnouncementCategoryConfigCreate(BaseModel):
    """Schema for creating a custom announcement category."""

    name: str  # Unique identifier (lowercase, no spaces)
    display_name: str  # Human-readable name
    description: Optional[str] = None
    auto_expire_hours: Optional[int] = None  # NULL = never expires
    default_notify_email: bool = True
    default_notify_push: bool = False
    icon: Optional[str] = None
    color: Optional[str] = None


class AnnouncementCategoryConfigUpdate(BaseModel):
    """Schema for updating a custom announcement category."""

    display_name: Optional[str] = None
    description: Optional[str] = None
    auto_expire_hours: Optional[int] = None
    default_notify_email: Optional[bool] = None
    default_notify_push: Optional[bool] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    is_active: Optional[bool] = None


class AnnouncementCategoryConfigResponse(BaseModel):
    """Response for announcement category config."""

    id: uuid.UUID
    name: str
    display_name: str
    description: Optional[str] = None
    auto_expire_hours: Optional[int] = None
    default_notify_email: bool
    default_notify_push: bool
    icon: Optional[str] = None
    color: Optional[str] = None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== CONTENT SCHEMAS =====
class ContentPostBase(BaseModel):
    """Base schema for content posts."""

    title: str
    summary: Optional[str] = None
    body: str  # Markdown content
    category: str  # swimming_tips/safety/breathing/technique/news/education/getting_started/community_culture/health_recovery
    featured_image_media_id: Optional[uuid.UUID] = None
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
    featured_image_media_id: Optional[uuid.UUID] = None
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
    featured_image_url: Optional[str] = None  # Resolved from media_id

    model_config = ConfigDict(from_attributes=True)

    @property
    def status(self) -> str:
        """Return 'published' or 'draft' based on is_published flag."""
        return "published" if self.is_published else "draft"

    def model_dump(self, **kwargs):
        """Include status in serialization."""
        data = super().model_dump(**kwargs)
        data["status"] = self.status
        return data


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


# ===== MESSAGING SCHEMAS =====
class MessageCreate(BaseModel):
    """Schema for sending a message."""

    subject: str
    body: str  # Plain text or HTML


class CohortMessageCreate(MessageCreate):
    """Schema for sending a message to all students in a cohort."""

    pass


class StudentMessageCreate(MessageCreate):
    """Schema for sending a message to an individual student."""

    pass


class MessageResponse(BaseModel):
    """Response after sending a message."""

    success: bool
    recipients_count: int
    message: str


class MessageLogResponse(BaseModel):
    """Response showing a sent message log entry."""

    id: uuid.UUID
    sender_id: uuid.UUID
    sender_name: Optional[str] = None
    recipient_type: str  # "cohort" or "student"
    recipient_id: uuid.UUID  # cohort_id or enrollment_id
    recipient_count: int
    subject: str
    sent_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ===== NOTIFICATION PREFERENCES SCHEMAS =====
class NotificationPreferencesBase(BaseModel):
    """Base schema for notification preferences."""

    # Email preferences
    email_announcements: bool = True
    email_session_reminders: bool = True
    email_academy_updates: bool = True
    email_payment_receipts: bool = True
    email_coach_messages: bool = True
    email_marketing: bool = False

    # Push notification preferences
    push_announcements: bool = True
    push_session_reminders: bool = True
    push_academy_updates: bool = True
    push_coach_messages: bool = True

    # Digest preferences
    weekly_digest: bool = True


class NotificationPreferencesUpdate(BaseModel):
    """Schema for updating notification preferences."""

    email_announcements: Optional[bool] = None
    email_session_reminders: Optional[bool] = None
    email_academy_updates: Optional[bool] = None
    email_payment_receipts: Optional[bool] = None
    email_coach_messages: Optional[bool] = None
    email_marketing: Optional[bool] = None
    push_announcements: Optional[bool] = None
    push_session_reminders: Optional[bool] = None
    push_academy_updates: Optional[bool] = None
    push_coach_messages: Optional[bool] = None
    weekly_digest: Optional[bool] = None


class NotificationPreferencesResponse(NotificationPreferencesBase):
    """Response schema for notification preferences."""

    id: uuid.UUID
    member_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
