import enum
import uuid
from datetime import datetime
from typing import Optional

from libs.db.base import Base
from sqlalchemy import Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class SessionLocation(str, enum.Enum):
    MAIN_POOL = "main_pool"
    DIVING_POOL = "diving_pool"
    KIDS_POOL = "kids_pool"
    OPEN_WATER = "open_water"


class SessionType(str, enum.Enum):
    CLUB_SESSION = "CLUB_SESSION"
    ACADEMY_CLASS = "ACADEMY_CLASS"
    MEETUP = "MEETUP"
    SPECIAL_EVENT = "SPECIAL_EVENT"


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Reference to event, but no FK constraint since events table is in different service
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    type: Mapped[SessionType] = mapped_column(
        SAEnum(SessionType, name="session_type_enum"),
        nullable=False,
        default=SessionType.CLUB_SESSION,
    )
    location: Mapped[SessionLocation] = mapped_column(
        SAEnum(SessionLocation, name="session_location_enum"), nullable=False
    )
    pool_fee: Mapped[float] = mapped_column(Float, default=0.0)
    ride_share_fee: Mapped[float] = mapped_column(Float, default=0.0)
    capacity: Mapped[int] = mapped_column(Integer, default=20)

    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Template tracking
    template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session_templates.id"), nullable=True
    )
    is_recurring_instance: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<Session {self.title} at {self.start_time}>"
