import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field

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
    featured_image_prompt: Optional[str] = Field(None, max_length=1200)
    tier_access: Literal["community", "club", "academy"] = "community"
    email_on_publish: bool = False


class ContentPostCreate(ContentPostBase):
    """Schema for creating a content post."""

    is_published: bool = False
    scheduled_for: Optional[datetime] = None


class ContentAIDraftCreate(BaseModel):
    """Schema for generating a content post draft with AI."""

    title: str = Field(..., min_length=4, max_length=180)
    brief: Optional[str] = Field(None, max_length=2000)
    category: str = "swimming_tips"
    tier_access: Literal["community", "club", "academy"] = "community"


class ContentPostUpdate(BaseModel):
    """Schema for updating a content post."""

    title: Optional[str] = None
    summary: Optional[str] = None
    body: Optional[str] = None
    category: Optional[str] = None
    featured_image_media_id: Optional[uuid.UUID] = None
    featured_image_prompt: Optional[str] = Field(None, max_length=1200)
    tier_access: Optional[Literal["community", "club", "academy"]] = None
    is_published: Optional[bool] = None
    scheduled_for: Optional[datetime] = None
    email_on_publish: Optional[bool] = None


class ContentPostResponse(ContentPostBase):
    """Content post response schema."""

    id: uuid.UUID
    is_published: bool
    published_at: Optional[datetime] = None
    scheduled_for: Optional[datetime] = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    comment_count: Optional[int] = 0
    featured_image_url: Optional[str] = None  # Resolved from media_id
    ai_request_id: Optional[uuid.UUID] = None
    ai_context_version: Optional[str] = None
    ai_model_used: Optional[str] = None
    email_sent_count: int = 0
    email_failed_count: int = 0
    email_in_progress_count: int = 0
    email_unknown_count: int = 0
    email_attempt_count: int = 0
    last_email_sent_at: Optional[datetime] = None
    email_recipient_snapshot_at: Optional[datetime] = None
    email_dispatch_last_attempt_at: Optional[datetime] = None
    email_dispatch_completed_at: Optional[datetime] = None
    email_dispatch_last_error: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def status(self) -> str:
        """Return 'published', 'scheduled', or 'draft' based on post state."""
        if self.is_published:
            return "published"
        if self.scheduled_for:
            return "scheduled"
        return "draft"


class WeeklyDigestConfigUpdate(BaseModel):
    featured_image_media_id: Optional[uuid.UUID] = None
    image_alt: Optional[str] = Field(None, min_length=3, max_length=240)
    section_intro: Optional[str] = Field(None, max_length=1000)
    default_gear_notes: Optional[str] = Field(None, max_length=1000)
    is_enabled: Optional[bool] = None


class WeeklyDigestConfigResponse(BaseModel):
    id: uuid.UUID
    audience: Literal["community", "club", "academy"]
    featured_image_media_id: Optional[uuid.UUID] = None
    featured_image_url: Optional[str] = None
    image_alt: str
    section_intro: Optional[str] = None
    default_gear_notes: Optional[str] = None
    is_enabled: bool
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WeeklyDigestStatsResponse(BaseModel):
    campaign_key: Optional[str] = None
    total: int = 0
    sent: int = 0
    failed: int = 0
    pending: int = 0
    uncertain: int = 0
    recipients_clicked: int = 0
    total_clicks: int = 0
    bookings_started: int = 0
    bookings_confirmed: int = 0


# ===== COMMENT SCHEMAS =====
class CommentCreate(BaseModel):
    """Schema for creating a comment."""

    content: str


class ContentCommentResponse(BaseModel):
    """Content comment response schema."""

    id: uuid.UUID
    post_id: uuid.UUID
    member_id: uuid.UUID
    member_name: Optional[str] = None
    content: str
    like_count: int = 0
    liked_by_me: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContentCommentReactionResponse(BaseModel):
    """Current reaction state for one content comment and member."""

    comment_id: uuid.UUID
    like_count: int
    liked_by_me: bool


class AnnouncementCommentResponse(BaseModel):
    """Announcement comment response schema."""

    id: uuid.UUID
    announcement_id: uuid.UUID
    member_id: uuid.UUID
    member_name: Optional[str] = None
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
# ===== NOTIFICATION SCHEMAS =====
class NotificationDispatchRequest(BaseModel):
    """Request to create and deliver notification(s) via the dispatcher."""

    type: str  # e.g. "order_confirmed"
    category: str  # e.g. "store", "sessions"
    member_ids: list[uuid.UUID]
    title: str
    body: Optional[str] = None
    action_url: Optional[str] = None
    icon: Optional[str] = None
    metadata: Optional[dict] = None
    channels: list[str] = ["in_app"]  # "in_app", "email"
    email_template: Optional[str] = None
    email_data: Optional[dict] = None
    expires_at: Optional[datetime] = None


class NotificationResponse(BaseModel):
    """Response for a single notification."""

    id: uuid.UUID
    type: str
    category: str
    title: str
    body: Optional[str] = None
    icon: Optional[str] = None
    action_url: Optional[str] = None
    metadata: Optional[dict] = None
    read_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    """Paginated notification list response."""

    items: list[NotificationResponse]
    total: int
    unread_count: int


class NotificationUnreadCountResponse(BaseModel):
    """Unread notification count response."""

    unread_count: int


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
    email_content_updates: bool = True
    email_birthday: bool = True

    # Push notification preferences
    push_announcements: bool = True
    push_session_reminders: bool = True
    push_academy_updates: bool = True
    push_coach_messages: bool = True

    # Session type subscriptions (for new session announcements)
    subscribe_community_sessions: bool = True
    subscribe_club_sessions: bool = True
    subscribe_event_sessions: bool = True

    # Reminder timing preferences
    reminder_24h_enabled: bool = True
    reminder_3h_enabled: bool = True

    # Digest preferences
    weekly_digest: bool = True
    weekly_session_digest: bool = True


class NotificationPreferencesUpdate(BaseModel):
    """Schema for updating notification preferences."""

    email_announcements: Optional[bool] = None
    email_session_reminders: Optional[bool] = None
    email_academy_updates: Optional[bool] = None
    email_payment_receipts: Optional[bool] = None
    email_coach_messages: Optional[bool] = None
    email_marketing: Optional[bool] = None
    email_content_updates: Optional[bool] = None
    email_birthday: Optional[bool] = None
    push_announcements: Optional[bool] = None
    push_session_reminders: Optional[bool] = None
    push_academy_updates: Optional[bool] = None
    push_coach_messages: Optional[bool] = None

    # Session type subscriptions
    subscribe_community_sessions: Optional[bool] = None
    subscribe_club_sessions: Optional[bool] = None
    subscribe_event_sessions: Optional[bool] = None

    # Reminder timing preferences
    reminder_24h_enabled: Optional[bool] = None
    reminder_3h_enabled: Optional[bool] = None

    # Digest preferences
    weekly_digest: Optional[bool] = None
    weekly_session_digest: Optional[bool] = None


class NotificationPreferencesResponse(NotificationPreferencesBase):
    """Response schema for notification preferences."""

    id: uuid.UUID
    member_auth_id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
