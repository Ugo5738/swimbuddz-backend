"""Pydantic schemas for seasonality forecasting endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# ── Request schemas ──


class GenerateForecastRequest(BaseModel):
    """Request body for generating a seasonality forecast."""

    forecast_year: int = Field(..., ge=2025, le=2035)
    force_regenerate: bool = False
    baseline_override: Optional[float] = Field(
        None, description="Override the auto-estimated baseline attendance"
    )
    trend_rate_override: Optional[float] = Field(
        None, description="Override monthly growth rate (e.g. 0.015 = 1.5%)"
    )


class IngestActualRequest(BaseModel):
    """Request body for manually entering monthly actuals."""

    year: int = Field(..., ge=2025, le=2035)
    month: int = Field(..., ge=1, le=12)
    active_members: int = Field(0, ge=0)
    total_sessions_held: int = Field(0, ge=0)
    total_attendance: int = Field(0, ge=0)
    new_signups: int = Field(0, ge=0)
    churned_members: int = Field(0, ge=0)
    total_revenue_ngn: int = Field(0, ge=0)
    attendance_by_type: Optional[dict] = None
    revenue_by_type: Optional[dict] = None


class SeedExternalFactorsRequest(BaseModel):
    """Request body for seeding external factors from Lagos priors."""

    year: int = Field(..., ge=2025, le=2035)


# ── Response schemas ──


class MonthForecastEntry(BaseModel):
    """Forecast detail for a single month."""

    month: int
    month_name: str
    seasonal_index: float
    trend_factor: float
    campaign_multiplier: float
    demand_level: str
    expected_demand: float
    lower_bound: float
    upper_bound: float
    status_label: str
    recommended_actions: list[str]
    key_factors: list[str]


class ForecastSummaryResponse(BaseModel):
    """Summary of a forecast run."""

    id: uuid.UUID
    forecast_year: int
    generated_at: datetime
    status: str
    months_of_real_data: int
    prior_weight: float


class ForecastDetailResponse(ForecastSummaryResponse):
    """Full forecast detail including monthly breakdown."""

    monthly_forecasts: list[MonthForecastEntry]
    model_params: dict


class MonthlyActualResponse(BaseModel):
    """Stored monthly actual metrics."""

    id: uuid.UUID
    year: int
    month: int
    active_members: int
    total_sessions_held: int
    total_attendance: int
    new_signups: int
    churned_members: int
    total_revenue_ngn: int
    source: str
    created_at: datetime


class MonthCalendarEntry(BaseModel):
    """Single month in the operational calendar."""

    month: int
    month_name: str
    demand_level: str
    expected_demand: float
    confidence_range: str
    status_label: str
    recommended_actions: list[str]
    key_factors: list[str]
