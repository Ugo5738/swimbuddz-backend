import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base
import enum


class RideShareOption(str, enum.Enum):
    NONE = "none"
    LEAD = "lead"
    JOIN = "join"


class RideArea(Base):
    __tablename__ = "ride_areas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String, nullable=False, unique=True
    )  # e.g. "Agor"
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
    description: Mapped[str] = mapped_column(
        String, nullable=True
    )  # e.g. "Apple Junction"

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


class RidePreference(Base):
    __tablename__ = "ride_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    ride_share_option: Mapped[RideShareOption] = mapped_column(
        SAEnum(RideShareOption, name="ride_share_option_enum"),
        default=RideShareOption.NONE,
        nullable=False,
    )
    needs_ride: Mapped[bool] = mapped_column(default=False)
    can_offer_ride: Mapped[bool] = mapped_column(default=False)
    ride_notes: Mapped[str] = mapped_column(String, nullable=True)
    pickup_location: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"\u003cRidePreference session={self.session_id} member={self.member_id} option={self.ride_share_option}\u003e"
