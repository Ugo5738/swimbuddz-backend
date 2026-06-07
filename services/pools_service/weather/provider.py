"""Weather data providers.

A thin abstraction so the upstream forecast API can be swapped without touching
the worker or the read API. The default is **Open-Meteo** (no API key, generous
free tier, what the team validated by hand).

LICENSING — IMPORTANT: Open-Meteo's free tier is **non-commercial**. For
production, point ``WEATHER_PROVIDER``/``WEATHER_API_KEY`` at a commercial
provider or self-host Open-Meteo. The abstraction below is where a second
provider gets wired in. See docs/design/WEATHER_SERVICE_DESIGN.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

_HOURLY_FIELDS = [
    "precipitation_probability",
    "precipitation",
    "temperature_2m",
    "weather_code",
]
_DAILY_FIELDS = [
    "precipitation_sum",
    "precipitation_probability_max",
    "temperature_2m_max",
    "temperature_2m_min",
    "weather_code",
]

_MAX_FORECAST_DAYS = 16  # Open-Meteo serves at most 16 forecast days


@dataclass
class ForecastData:
    """Normalized forecast payload returned by any provider."""

    provider: str
    timezone: str
    hourly: dict
    daily: Optional[dict] = None


class WeatherProvider(Protocol):
    """Interface every weather provider implements."""

    name: str

    async def fetch_forecast(
        self, *, latitude: float, longitude: float, days: int, timezone: str
    ) -> ForecastData: ...


class OpenMeteoProvider:
    """Open-Meteo implementation (https://open-meteo.com/)."""

    name = "open-meteo"

    def __init__(
        self,
        *,
        base_url: str = _OPEN_METEO_URL,
        api_key: str = "",
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout

    async def fetch_forecast(
        self, *, latitude: float, longitude: float, days: int, timezone: str
    ) -> ForecastData:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(_HOURLY_FIELDS),
            "daily": ",".join(_DAILY_FIELDS),
            "timezone": timezone,
            "forecast_days": max(1, min(int(days), _MAX_FORECAST_DAYS)),
        }
        if self._api_key:
            params["apikey"] = self._api_key

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._base_url, params=params)
            resp.raise_for_status()
            data = resp.json()

        return parse_open_meteo(data, fallback_timezone=timezone)


def parse_open_meteo(data: dict, *, fallback_timezone: str = "Africa/Lagos") -> ForecastData:
    """Normalize a raw Open-Meteo JSON response into ForecastData.

    Split from the HTTP call so it can be unit-tested without a network.
    """
    return ForecastData(
        provider=OpenMeteoProvider.name,
        timezone=data.get("timezone") or fallback_timezone,
        hourly=data.get("hourly") or {},
        daily=data.get("daily"),
    )


def get_provider() -> WeatherProvider:
    """Return the configured provider instance (only Open-Meteo today)."""
    settings = get_settings()
    provider = (settings.WEATHER_PROVIDER or "open-meteo").lower()
    api_key = settings.WEATHER_API_KEY or ""

    if provider not in ("open-meteo", ""):
        logger.warning(
            "weather: unknown WEATHER_PROVIDER=%r, falling back to open-meteo",
            provider,
        )
    return OpenMeteoProvider(api_key=api_key)
