from sqlalchemy import Column, String, Integer, Boolean, Time, DateTime, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid
import enum

from libs.db.base import Base
from services.sessions_service.models import SessionType


class DayOfWeek(enum.IntEnum):
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


class SessionTemplate(Base):
    __tablename__ = "session_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    location = Column(String, nullable=False)
    type = Column(
        SAEnum(SessionType, name="session_type_enum"),
        nullable=False,
        default=SessionType.COMMUNITY,  # default to community-facing session
    )
    pool_fee = Column(Integer, nullable=False, default=0)
    ride_share_fee = Column(Integer, nullable=False, default=0)
    capacity = Column(Integer, nullable=False, default=20)
    
    # Ride Share Configuration (List of ride areas and their settings)
    ride_share_config = Column(JSONB, nullable=True)

    # Recurrence pattern
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    start_time = Column(Time, nullable=False)  # Time of day (e.g., 09:00)
    duration_minutes = Column(Integer, nullable=False)  # Duration in minutes

    # Auto-generation
    auto_generate = Column(Boolean, default=False)  # Auto-create sessions weekly

    # Status
    is_active = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<SessionTemplate {self.title} - {DayOfWeek(self.day_of_week).name}s>"
