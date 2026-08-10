"""Operating geography and effective-dated cost catalogue."""

import uuid
from datetime import date, datetime, time
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class OperatingArea(Base):
    """Hierarchical operational geography, separate from language locale."""

    __tablename__ = "operating_areas"
    __table_args__ = (
        UniqueConstraint(
            "parent_id",
            "slug",
            name="uq_operating_areas_parent_slug",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    area_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="locality", server_default="locality"
    )  # country/market/commercial_band/locality
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("operating_areas.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    country_code: Mapped[str] = mapped_column(
        String(2), nullable=False, default="NG", server_default="NG"
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Africa/Lagos",
        server_default="Africa/Lagos",
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="NGN", server_default="NGN"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PoolRate(Base):
    """Effective facility charge for a pool and activity scope."""

    __tablename__ = "pool_rates"
    __table_args__ = (
        CheckConstraint(
            "day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)",
            name="ck_pool_rates_day_of_week",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="all", server_default="all"
    )
    charge_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="NGN", server_default="NGN"
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    day_of_week: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    starts_after: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    ends_before: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    minimum_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class OperatingCostRate(Base):
    """Effective non-pool input such as refreshments or logistics."""

    __tablename__ = "operating_cost_rates"
    __table_args__ = (
        CheckConstraint(
            "NOT (operating_area_id IS NOT NULL AND pool_id IS NOT NULL)",
            name="ck_operating_cost_rates_one_scope",
        ),
        CheckConstraint(
            "day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)",
            name="ck_operating_cost_rates_day_of_week",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    operating_area_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("operating_areas.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pools.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    supplier_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    activity_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="all", server_default="all"
    )
    charge_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="NGN", server_default="NGN"
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    day_of_week: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    starts_after: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    ends_before: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    minimum_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
