import enum
import uuid
from datetime import datetime

from libs.db.base import Base
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class RideShareOption(str, enum.Enum):
    NONE = "none"
    LEAD = "lead"
    JOIN = "join"


class RideArea(Base):
    __tablename__ = "ride_areas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<RideArea {self.name}>"


class PickupLocation(Base):
    __tablename__ = "pickup_locations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=True)

    area_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ride_areas.id"), nullable=False
    )

    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<PickupLocation {self.name}>"


class RouteInfo(Base):
    __tablename__ = "route_info"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    origin_area_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ride_areas.id"), nullable=True
    )

    origin_pickup_location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pickup_locations.id"), nullable=True
    )

    # Destination (matches SessionLocation enum values or custom)
    destination: Mapped[str] = mapped_column(String, nullable=False)
    destination_name: Mapped[str] = mapped_column(
        String, nullable=False
    )  # e.g. "Rowe Park, Yaba"

    distance_text: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "13.7 km"
    duration_text: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "44 mins"
    departure_offset_minutes: Mapped[int] = mapped_column(
        Integer, default=120
    )  # e.g. 120

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<RouteInfo Area={self.origin_area_id} Loc={self.origin_pickup_location_id} -> {self.destination}>"


class SessionRideConfig(Base):
    """Link between a session and a ride area with session-specific configuration."""

    __tablename__ = "session_ride_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    ride_area_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ride_areas.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Session-specific overrides
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    capacity: Mapped[int] = mapped_column(Integer, default=4)
    departure_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class RideBooking(Base):
    """Member's ride booking for a session."""

    __tablename__ = "ride_bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    session_ride_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_ride_configs.id", ondelete="CASCADE"),
        nullable=False,
    )
    pickup_location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pickup_locations.id"), nullable=False
    )
    assigned_ride_number: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("session_id", "member_id", name="uq_session_member_booking"),
    )

    def __repr__(self):
        return f"\u003cRideBooking session={self.session_id} member={self.member_id} pickup_location={self.pickup_location_id}\u003e"
