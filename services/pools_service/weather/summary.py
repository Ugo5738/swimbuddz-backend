"""Canonical weather aggregation for an arbitrary local-time window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WeatherKind = Literal["clear", "partly", "cloudy", "rain", "storm"]

WMO_LABELS = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm",
    99: "Thunderstorm",
}


@dataclass(frozen=True)
class WeatherWindowSummary:
    """Provider-independent facts and shared interpretation for a time window."""

    window_start: datetime
    window_end: datetime
    max_precipitation_probability: int
    total_precipitation_mm: float
    temperature_high_c: float | None
    temperature_low_c: float | None
    representative_weather_code: int
    kind: WeatherKind
    condition_text: str
    explanation: str


def condition_label(code: int) -> str:
    """Map a WMO weather code to the shared member-facing condition label."""
    return WMO_LABELS.get(code, "Cloudy")


def classify_weather(code: int, max_probability: int) -> WeatherKind:
    """Classify a representative code and rain risk for icon/tone selection."""
    if code >= 95:
        return "storm"
    if code >= 51 or max_probability >= 60:
        return "rain"
    if code >= 45 or max_probability >= 30:
        return "cloudy"
    if code >= 1:
        return "partly"
    return "clear"


def weather_explanation(
    kind: WeatherKind,
    max_probability: int,
    total_precipitation_mm: float,
) -> str:
    """Return the shared concise interpretation of a summarized window."""
    if kind == "storm":
        return "Thunderstorm possible — sessions pause if there's lightning."
    if max_probability < 30:
        return "Looks dry for your session."
    if total_precipitation_mm >= 5:
        return "Steady rain likely during the session."
    if kind == "rain" or total_precipitation_mm >= 1:
        return "Light rain likely — warm and swimmable."
    return "Cloudy with a slight chance of drizzle."


def _number(values: object, index: int) -> float | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    return float(value) if isinstance(value, (int, float)) else None


def _forecast_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Africa/Lagos")


def _local_datetime(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone)
    return value.astimezone(timezone)


def _parse_forecast_hour(value: object, timezone: ZoneInfo) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def summarize_weather_window(
    hourly: dict,
    *,
    starts_at: datetime,
    ends_at: datetime,
    timezone: str,
) -> WeatherWindowSummary | None:
    """Aggregate forecast hours overlapping the inclusive session-hour window."""
    forecast_timezone = _forecast_timezone(timezone)
    local_start = _local_datetime(starts_at, forecast_timezone)
    local_end = _local_datetime(ends_at, forecast_timezone)
    if local_end < local_start:
        raise ValueError("ends_at must be on or after starts_at")
    first_hour = local_start.replace(minute=0, second=0, microsecond=0)
    last_hour = local_end.replace(minute=0, second=0, microsecond=0)

    times = hourly.get("time") if isinstance(hourly, dict) else None
    if not isinstance(times, list):
        return None

    indices = []
    for index, value in enumerate(times):
        forecast_hour = _parse_forecast_hour(value, forecast_timezone)
        if forecast_hour is not None and first_hour <= forecast_hour <= last_hour:
            indices.append(index)
    if not indices:
        return None

    max_probability = 0
    total_precipitation = 0.0
    temperature_high: float | None = None
    temperature_low: float | None = None
    peak_index = indices[0]
    peak_probability = -1

    for index in indices:
        probability = int(_number(hourly.get("precipitation_probability"), index) or 0)
        max_probability = max(max_probability, probability)
        if probability > peak_probability:
            peak_probability = probability
            peak_index = index

        total_precipitation += _number(hourly.get("precipitation"), index) or 0
        temperature = _number(hourly.get("temperature_2m"), index)
        if temperature is not None:
            temperature_high = (
                temperature
                if temperature_high is None
                else max(temperature_high, temperature)
            )
            temperature_low = (
                temperature
                if temperature_low is None
                else min(temperature_low, temperature)
            )

    code = int(_number(hourly.get("weather_code"), peak_index) or 0)
    total_mm = round(total_precipitation, 1)
    kind = classify_weather(code, max_probability)
    return WeatherWindowSummary(
        window_start=local_start,
        window_end=local_end,
        max_precipitation_probability=max_probability,
        total_precipitation_mm=total_mm,
        temperature_high_c=temperature_high,
        temperature_low_c=temperature_low,
        representative_weather_code=code,
        kind=kind,
        condition_text=condition_label(code),
        explanation=weather_explanation(kind, max_probability, total_mm),
    )
