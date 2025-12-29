import uuid
from datetime import datetime
import enum

from sqlalchemy import String, Text, Boolean, DateTime, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base


class AnnouncementCategory(str, enum.Enum):
    RAIN_UPDATE = "rain_update"
    SCHEDULE_CHANGE = "schedule_change"
    EVENT = "event"
    COMPETITION = "competition"
    GENERAL = "general"


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

    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<Announcement {self.title}>"


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
    )  # swimming_tips/safety/breathing/technique/news/education
    featured_image_url: Mapped[str] = mapped_column(String, nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    tier_access: Mapped[str] = mapped_column(
        String, default="community"
    )  # community/club/academy
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
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
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
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
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<AnnouncementComment announcement={self.announcement_id} member={self.member_id}>"
