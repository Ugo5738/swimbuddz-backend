"""Pool model — comprehensive pool screening and partnership registry."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, DateTime, Float, Integer, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.pools_service.models.enums import (
    IndoorOutdoor,
    PartnershipStatus,
    PoolType,
    enum_values,
)


class Pool(Base):
    """Pool screening & partnership entity.

    Contains all 30 screening fields used by the SwimBuddz team to
    evaluate and manage pool partnerships across Lagos and beyond.
    """

    __tablename__ = "pools"

    # ── Identity ──────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    location_area: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )  # e.g., "Yaba", "Lekki Phase 1", "Victoria Island"
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ── Contact ───────────────────────────────────────────────────────────
    contact_person: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── Physical ──────────────────────────────────────────────────────────
    pool_length_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    depth_min_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    depth_max_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    number_of_lanes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    indoor_outdoor: Mapped[Optional[IndoorOutdoor]] = mapped_column(
        SAEnum(
            IndoorOutdoor,
            values_callable=enum_values,
            name="pool_indoor_outdoor_enum",
        ),
        nullable=True,
    )
    max_swimmers_capacity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Scores (1-5 scale) ────────────────────────────────────────────────
    water_quality: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    good_for_beginners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    good_for_training: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ease_of_access: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    management_cooperation: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    partnership_potential: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    overall_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Availability ──────────────────────────────────────────────────────
    available_days_times: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )  # e.g., {"Mon": "6am-8am", "Wed": "6am-8am", "Sat": "7am-12pm"}
    exclusive_lanes_available: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )

    # ── Pricing ───────────────────────────────────────────────────────────
    price_per_swimmer_ngn: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    flat_session_fee_ngn: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    group_discount_available: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )

    # ── Facilities ────────────────────────────────────────────────────────
    has_changing_rooms: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_showers: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_lockers: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_parking: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_lifeguard: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # ── Operations ────────────────────────────────────────────────────────
    video_content_allowed: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    trial_session_possible: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )

    # ── Partnership ───────────────────────────────────────────────────────
    partnership_status: Mapped[PartnershipStatus] = mapped_column(
        SAEnum(
            PartnershipStatus,
            values_callable=enum_values,
            name="pool_partnership_status_enum",
        ),
        default=PartnershipStatus.PROSPECT,
        server_default="prospect",
        index=True,
    )

    # ── Meta ──────────────────────────────────────────────────────────────
    pool_type: Mapped[Optional[PoolType]] = mapped_column(
        SAEnum(
            PoolType,
            values_callable=enum_values,
            name="pool_type_enum",
        ),
        nullable=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<Pool {self.name} ({self.location_area})>"
