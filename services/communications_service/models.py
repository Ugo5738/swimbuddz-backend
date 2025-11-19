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
        nullable=False
    )
    
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<Announcement {self.title}>"
