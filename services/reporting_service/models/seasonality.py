"""Seasonality forecasting database models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.reporting_service.models.enums import (
    DataSource,
    ForecastStatus,
    enum_values,
)


class MonthlyActual(Base):
    """Historical actual metrics for a given month."""

    __tablename__ = "monthly_actuals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    # Core demand metrics
    active_members: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_sessions_held: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_attendance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_signups: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    churned_members: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_revenue_ngn: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Per-product-line breakdowns
    attendance_by_type: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    revenue_by_type: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # External context (populated by priors or manual entry)
    rainfall_mm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_school_term: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_exam_period: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    holiday_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Metadata
    source: Mapped[str] = mapped_column(
        SAEnum(
            DataSource,
            name="data_source_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=DataSource.SYSTEM,
        nullable=False,
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_monthly_actual_year_month"),
    )

    def __repr__(self) -> str:
        return f"<MonthlyActual {self.year}-{self.month:02d} attendance={self.total_attendance}>"


class SeasonalityForecast(Base):
    """Stored forecast run for a given year."""

    __tablename__ = "seasonality_forecasts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    forecast_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    status: Mapped[str] = mapped_column(
        SAEnum(
            ForecastStatus,
            name="forecast_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=ForecastStatus.PENDING,
        nullable=False,
    )

    # Model parameters snapshot for reproducibility
    model_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Full 12-month forecast as JSONB array
    monthly_forecasts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Data quality metadata
    months_of_real_data: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prior_weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # Output file paths (relative)
    markdown_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    csv_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    html_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<SeasonalityForecast {self.forecast_year} ({self.status})>"


class ExternalFactor(Base):
    """Lagos-specific external factors for a given month."""

    __tablename__ = "external_factors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    # Rainfall
    rainfall_mm: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rainfall_category: Mapped[str] = mapped_column(
        String, default="dry", nullable=False
    )

    # School calendar
    school_term_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    exam_period: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    school_holiday: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Public holidays
    holiday_names: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    holiday_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Economic cycle
    salary_week: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Campaign/event overrides
    campaign_names: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    campaign_effect_multiplier: Mapped[float] = mapped_column(
        Float, default=1.0, nullable=False
    )

    # Provenance
    source: Mapped[str] = mapped_column(
        SAEnum(
            DataSource,
            name="data_source_enum",
            values_callable=enum_values,
            validate_strings=True,
            create_constraint=False,
        ),
        default=DataSource.PRIOR,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_external_factor_year_month"),
    )

    def __repr__(self) -> str:
        return (
            f"<ExternalFactor {self.year}-{self.month:02d} rain={self.rainfall_mm}mm>"
        )
