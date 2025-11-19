import uuid
from datetime import datetime
import enum

from sqlalchemy import String, Integer, Float, DateTime, Enum as SAEnum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base


class SessionLocation(str, enum.Enum):
    MAIN_POOL = "main_pool"
    DIVING_POOL = "diving_pool"
    KIDS_POOL = "kids_pool"
    OPEN_WATER = "open_water"


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    location: Mapped[SessionLocation] = mapped_column(
        SAEnum(SessionLocation, name="session_location_enum"), nullable=False
    )
    pool_fee: Mapped[float] = mapped_column(Float, default=0.0)
    capacity: Mapped[int] = mapped_column(Integer, default=20)
    
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<Session {self.title} at {self.start_time}>"
