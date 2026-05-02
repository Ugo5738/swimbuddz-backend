"""Flywheel metrics models — cross-service funnel, cohort fill, and wallet ecosystem snapshots.

These tables answer the question: is the SwimBuddz ecosystem actually flowing?
- CohortFillSnapshot: per-cohort fill state (operational, refreshed daily)
- FunnelConversionSnapshot: community→club and club→academy conversion rates
- WalletEcosystemSnapshot: cross-service wallet spend (validates wallet-as-glue thesis)
"""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.reporting_service.models.enums import FunnelStage, enum_values


class CohortFillSnapshot(Base):
    """Per-cohort fill rate snapshot, refreshed daily.

    Operational metric: tells the founder which cohorts are at risk of
    underfilling and where to push enrollment.
    """

    __tablename__ = "cohort_fill_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    cohort_name: Mapped[str] = mapped_column(String, nullable=False)
    program_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    active_enrollments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pending_approvals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    waitlist_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fill_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    starts_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cohort_status: Mapped[str] = mapped_column(String, nullable=False)
    days_until_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    snapshot_taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "cohort_id", "snapshot_taken_at", name="uq_cohort_fill_per_run"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<CohortFillSnapshot {self.cohort_name} "
            f"{self.active_enrollments}/{self.capacity} "
            f"({self.fill_rate:.0%})>"
        )


class FunnelConversionSnapshot(Base):
    """Cross-service funnel conversion snapshot.

    Tracks community→club and club→academy conversion for rolling cohort
    periods. The source_count is "how many entered the source layer in this
    period"; converted_count is "how many crossed to the next layer within
    the observation window."
    """

    __tablename__ = "funnel_conversion_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    funnel_stage: Mapped[FunnelStage] = mapped_column(
        SAEnum(
            FunnelStage,
            name="funnel_stage_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )
    cohort_period: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )  # e.g. "2026-Q1", "2025"
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    observation_window_days: Mapped[int] = mapped_column(Integer, nullable=False)

    source_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    converted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversion_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    breakdown_by_source: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    snapshot_taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "funnel_stage",
            "cohort_period",
            "snapshot_taken_at",
            name="uq_funnel_per_period_per_run",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<FunnelConversionSnapshot {self.funnel_stage} "
            f"{self.cohort_period} "
            f"{self.converted_count}/{self.source_count} "
            f"({self.conversion_rate:.0%})>"
        )


class WalletEcosystemSnapshot(Base):
    """Wallet cross-service usage snapshot.

    Validates the wallet-as-glue thesis: are members spending across multiple
    services, or only one? If most members spend on a single service category,
    wallet is overhead, not ecosystem glue.
    """

    __tablename__ = "wallet_ecosystem_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_days: Mapped[int] = mapped_column(Integer, nullable=False)

    active_wallet_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    single_service_users: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    cross_service_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cross_service_rate: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )

    total_bubbles_spent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_topup_bubbles: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    spend_distribution: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    snapshot_taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "period_start",
            "period_end",
            "snapshot_taken_at",
            name="uq_wallet_ecosystem_per_period_per_run",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<WalletEcosystemSnapshot {self.period_start}→{self.period_end} "
            f"{self.cross_service_users}/{self.active_wallet_users} cross "
            f"({self.cross_service_rate:.0%})>"
        )
