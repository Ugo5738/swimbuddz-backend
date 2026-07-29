"""Pydantic schemas for the weather module."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.pools_service.weather.summary import WeatherKind


class WeatherSnapshotResponse(BaseModel):
    """A cached forecast for one location.

    ``hourly``/``daily`` are returned as-is from the provider (Open-Meteo's
    parallel-array shape). When the caller passes ``?date=YYYY-MM-DD`` the
    arrays are trimmed server-side to just that day to save mobile bandwidth.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    location_key: str
    latitude: float
    longitude: float
    pool_id: Optional[uuid.UUID] = None
    label: Optional[str] = None
    provider: str
    timezone: str
    forecast_days: int
    hourly: dict
    daily: Optional[dict] = None
    fetched_at: datetime
    expires_at: datetime
    stale: bool = False  # computed in the router: True when past TTL


class WeatherWindowSummaryResponse(BaseModel):
    """Canonical provider-independent weather summary for a requested window."""

    pool_id: Optional[uuid.UUID] = None
    timezone: str
    forecast_days: int
    stale: bool
    window_start: datetime
    window_end: datetime
    max_precipitation_probability: int = Field(ge=0, le=100)
    total_precipitation_mm: float = Field(ge=0)
    temperature_high_c: Optional[float]
    temperature_low_c: Optional[float]
    representative_weather_code: int
    kind: WeatherKind
    condition_text: str
    explanation: str


class WeatherRefreshResult(BaseModel):
    """Summary of a pre-fetch refresh run (worker or admin-triggered)."""

    refreshed: int = 0
    failed: int = 0
    skipped_no_coords: int = 0
    pool_ids: list[str] = Field(default_factory=list)
