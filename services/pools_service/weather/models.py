"""WeatherSnapshot — a cached multi-day hourly forecast for one location.

Owned by pools_service (table name kept generic so it reads naturally even
though it sits in the pools schema namespace).
"""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class WeatherSnapshot(Base):
    """A stored multi-day hourly forecast for one geographic location.

    One row per normalized location (``location_key``). The pre-fetch worker
    upserts these on a schedule for every active pool; the read API serves them
    directly and falls back to a live provider fetch (cache-aside) for
    arbitrary coordinates not yet cached.

    ``pool_id`` points at a pool in the same service — but is kept as a plain
    UUID (no FK) so the weather module stays a self-contained, liftable concern.
    """

    __tablename__ = "weather_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Normalized "lat,lon" rounded to ~1km — the upsert key + dedup handle.
    location_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    label: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )  # e.g. "Yaba", "Lekki Phase 1"

    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="open-meteo"
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Africa/Lagos"
    )
    forecast_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=14, server_default="14"
    )

    hourly: Mapped[dict] = mapped_column(JSONB, nullable=False)
    daily: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<WeatherSnapshot {self.location_key} "
            f"pool={self.pool_id} fetched={self.fetched_at:%Y-%m-%d %H:%M}>"
        )
