import enum
import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class AnnouncementCategory(str, enum.Enum):
    """Default announcement categories. Custom categories can be added via AnnouncementCategoryConfig."""

    RAIN_UPDATE = "rain_update"
    SCHEDULE_CHANGE = "schedule_change"
    ACADEMY_UPDATE = "academy_update"
    EVENT = "event"
    COMPETITION = "competition"
    GENERAL = "general"
    CUSTOM = "custom"  # For user-defined categories


class AnnouncementStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class AnnouncementAudience(str, enum.Enum):
    COMMUNITY = "community"
    CLUB = "club"
    ACADEMY = "academy"


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    category: Mapped[AnnouncementCategory] = mapped_column(
        SAEnum(AnnouncementCategory, name="announcement_category_enum"),
        default=AnnouncementCategory.GENERAL,
        nullable=False,
    )
    # For custom categories, store the custom category name
    custom_category: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    status: Mapped[AnnouncementStatus] = mapped_column(
        SAEnum(AnnouncementStatus, name="announcement_status_enum"),
        default=AnnouncementStatus.PUBLISHED,
        nullable=False,
    )
    audience: Mapped[AnnouncementAudience] = mapped_column(
        SAEnum(AnnouncementAudience, name="announcement_audience_enum"),
        default=AnnouncementAudience.COMMUNITY,
        nullable=False,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_push: Mapped[bool] = mapped_column(Boolean, default=True)

    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    # Scheduled publishing: if set, announcement will auto-publish at this time
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<Announcement {self.title}>"


class AnnouncementRead(Base):
    """Tracks which members have read/acknowledged which announcements."""

    __tablename__ = "announcement_reads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    announcement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # Optional: track if member explicitly acknowledged (clicked "Got it" button)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self):
        return f"<AnnouncementRead announcement={self.announcement_id} member={self.member_id}>"


class AnnouncementCategoryConfig(Base):
    """Configurable announcement categories - allows admins to add custom categories."""

    __tablename__ = "announcement_category_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Auto-expiry configuration (hours). NULL = never expires
    auto_expire_hours: Mapped[Optional[int]] = mapped_column(nullable=True)

    # Default notification settings
    default_notify_email: Mapped[bool] = mapped_column(Boolean, default=True)
    default_notify_push: Mapped[bool] = mapped_column(Boolean, default=False)

    # Icon/color for UI
    icon: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # e.g., "bell", "alert", "calendar"
    color: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # e.g., "red", "blue", "cyan"

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return f"<AnnouncementCategoryConfig {self.name}>"


class MemberRef(Base):
    """Reference to shared members table without cross-service imports."""

    __tablename__ = "members"
    __table_args__ = {"extend_existing": True, "info": {"skip_autogenerate": True}}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class ContentPost(Base):
    """Educational content, tips, and articles for the community."""

    __tablename__ = "content_posts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)  # Markdown support
    category: Mapped[str] = mapped_column(
        String, nullable=False
    )  # swimming_tips/safety/breathing/technique/news/education/getting_started/community_culture/health_recovery
    featured_image_media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    tier_access: Mapped[str] = mapped_column(
        String, default="community"
    )  # community/club/academy
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<ContentPost {self.title}>"


class ContentComment(Base):
    """Comments on content posts."""

    __tablename__ = "content_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<ContentComment post={self.post_id} member={self.member_id}>"


class AnnouncementComment(Base):
    """Comments on announcements."""

    __tablename__ = "announcement_comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    announcement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<AnnouncementComment announcement={self.announcement_id} member={self.member_id}>"


class MessageRecipientType(str, enum.Enum):
    """Type of message recipient."""

    COHORT = "cohort"
    STUDENT = "student"


class MessageLog(Base):
    """Log of sent messages for audit trail."""

    __tablename__ = "message_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )  # member_id of sender (coach/admin)
    recipient_type: Mapped[MessageRecipientType] = mapped_column(
        SAEnum(MessageRecipientType, name="message_recipient_type_enum"),
        nullable=False,
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )  # cohort_id or enrollment_id
    recipient_count: Mapped[int] = mapped_column(nullable=False, default=1)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return f"<MessageLog sender={self.sender_id} to={self.recipient_type}:{self.recipient_id}>"


class NotificationPreferences(Base):
    """Member notification preferences for email and push notifications."""

    __tablename__ = "notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )

    # Email preferences
    email_announcements: Mapped[bool] = mapped_column(Boolean, default=True)
    email_session_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    email_academy_updates: Mapped[bool] = mapped_column(Boolean, default=True)
    email_payment_receipts: Mapped[bool] = mapped_column(Boolean, default=True)
    email_coach_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    email_marketing: Mapped[bool] = mapped_column(Boolean, default=False)

    # Push notification preferences (for future mobile app)
    push_announcements: Mapped[bool] = mapped_column(Boolean, default=True)
    push_session_reminders: Mapped[bool] = mapped_column(Boolean, default=True)
    push_academy_updates: Mapped[bool] = mapped_column(Boolean, default=True)
    push_coach_messages: Mapped[bool] = mapped_column(Boolean, default=True)

    # Digest preferences
    weekly_digest: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<NotificationPreferences member={self.member_id}>"
