"""Weather enrichment shared by session email contexts."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import internal_get

logger = get_logger(__name__)

DEFAULT_TIMEZONE = ZoneInfo("Africa/Lagos")
WEATHER_FORECAST_HORIZON_DAYS = 14

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


def session_timezone(session: dict) -> ZoneInfo:
    try:
        return ZoneInfo(session.get("timezone") or "Africa/Lagos")
    except Exception:
        return DEFAULT_TIMEZONE


def _weather_num(values: object, index: int) -> float | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    return value if isinstance(value, (int, float)) else None


def _condition_label(code: int) -> str:
    return WMO_LABELS.get(code, "Cloudy")


def _weather_kind(code: int, max_prob: int) -> str:
    if code >= 95:
        return "storm"
    if code >= 51 or max_prob >= 60:
        return "rain"
    if code >= 45 or max_prob >= 30:
        return "cloudy"
    if code >= 1:
        return "partly"
    return "clear"


def _weather_explanation(kind: str, max_prob: int, total_mm: float) -> str:
    if kind == "storm":
        return "Thunderstorm possible - sessions pause if there is lightning."
    if max_prob < 30:
        return "Looks dry for your session."
    if total_mm >= 5:
        return "Steady rain likely during the session."
    if kind == "rain" or total_mm >= 1:
        return "Light rain likely - warm and swimmable."
    return "Cloudy with a slight chance of drizzle."


def _format_mm(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def summarize_session_weather(
    forecast: dict,
    *,
    starts_at: datetime,
    ends_at: datetime,
) -> dict | None:
    """Summarize cached hourly forecast data for the session's own hours."""
    hourly = forecast.get("hourly") if isinstance(forecast, dict) else None
    if not isinstance(hourly, dict):
        return None

    times = hourly.get("time")
    if not isinstance(times, list):
        return None

    date_prefix = starts_at.strftime("%Y-%m-%d")
    start_hour = starts_at.hour
    end_hour = ends_at.hour
    if ends_at.date() != starts_at.date() or end_hour < start_hour:
        end_hour = 23

    indices: list[int] = []
    for index, value in enumerate(times):
        if not isinstance(value, str) or not value.startswith(date_prefix):
            continue
        try:
            hour = int(value[11:13])
        except ValueError:
            continue
        if start_hour <= hour <= end_hour:
            indices.append(index)

    if not indices:
        return None

    max_prob = 0
    total_precip = 0.0
    temp_high: float | None = None
    peak_idx = indices[0]
    peak_prob = -1

    for index in indices:
        probability = int(
            _weather_num(hourly.get("precipitation_probability"), index) or 0
        )
        max_prob = max(max_prob, probability)
        if probability > peak_prob:
            peak_prob = probability
            peak_idx = index

        total_precip += _weather_num(hourly.get("precipitation"), index) or 0
        temperature = _weather_num(hourly.get("temperature_2m"), index)
        if temperature is not None:
            temp_high = (
                temperature if temp_high is None else max(temp_high, temperature)
            )

    code = int(_weather_num(hourly.get("weather_code"), peak_idx) or 0)
    total_mm = round(total_precip, 1)
    kind = _weather_kind(code, max_prob)
    return {
        "condition_text": _condition_label(code),
        "temperature_text": f"{round(temp_high)}°C" if temp_high is not None else "",
        "rain_chance_text": f"{max_prob}% chance of rain",
        "rainfall_text": f"~{_format_mm(total_mm)}mm during session",
        "explanation": _weather_explanation(kind, max_prob, total_mm),
    }


async def get_session_weather_summary(session: dict) -> dict | None:
    """Fetch and summarize weather without blocking the surrounding email job."""
    pool_id = session.get("pool_id")
    if not pool_id:
        return None

    local_tz = session_timezone(session)
    now = utc_now()
    starts_at = datetime.fromisoformat(session["starts_at"]).astimezone(local_tz)
    days_until = (starts_at.date() - now.astimezone(local_tz).date()).days
    if days_until < 0 or days_until > WEATHER_FORECAST_HORIZON_DAYS:
        return None

    ends_at = datetime.fromisoformat(session["ends_at"]).astimezone(local_tz)
    settings = get_settings()
    try:
        response = await internal_get(
            service_url=settings.POOLS_SERVICE_URL,
            path=f"/weather/pools/{pool_id}",
            calling_service="communications",
            params={"date": starts_at.strftime("%Y-%m-%d")},
            timeout=5.0,
        )
    except Exception as exc:
        logger.warning(
            "Failed to fetch weather for session %s: %s",
            session.get("id"),
            exc,
        )
        return None

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        logger.warning(
            "Weather lookup for session %s returned %s",
            session.get("id"),
            response.status_code,
        )
        return None

    return summarize_session_weather(
        response.json(),
        starts_at=starts_at,
        ends_at=ends_at,
    )
