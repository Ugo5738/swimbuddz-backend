"""Weather enrichment shared by session email contexts."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_get

logger = get_logger(__name__)

DEFAULT_TIMEZONE = ZoneInfo("Africa/Lagos")


def session_timezone(session: dict) -> ZoneInfo:
    try:
        return ZoneInfo(session.get("timezone") or "Africa/Lagos")
    except Exception:
        return DEFAULT_TIMEZONE


def _session_datetime(value: str, timezone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _format_mm(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def format_session_weather_summary(summary: dict) -> dict | None:
    """Format the pools service's neutral summary for email templates."""
    if not isinstance(summary, dict):
        return None

    max_probability = summary.get("max_precipitation_probability")
    total_precipitation = summary.get("total_precipitation_mm")
    temperature_high = summary.get("temperature_high_c")
    if not isinstance(max_probability, (int, float)) or not isinstance(
        total_precipitation, (int, float)
    ):
        return None

    total_mm = float(total_precipitation)
    return {
        "condition_text": str(summary.get("condition_text") or "Cloudy"),
        "temperature_text": (
            f"{round(temperature_high)}°C"
            if isinstance(temperature_high, (int, float))
            else ""
        ),
        "rain_chance_text": f"{round(max_probability)}% chance of rain",
        "rainfall_text": f"~{_format_mm(total_mm)}mm during session",
        "explanation": str(summary.get("explanation") or ""),
    }


async def get_session_weather_summary(session: dict) -> dict | None:
    """Fetch canonical weather facts and format them without blocking email."""
    pool_id = session.get("pool_id")
    if not pool_id:
        return None

    local_tz = session_timezone(session)
    starts_at = _session_datetime(session["starts_at"], local_tz)
    ends_at = _session_datetime(session["ends_at"], local_tz)
    settings = get_settings()
    try:
        response = await internal_get(
            service_url=settings.POOLS_SERVICE_URL,
            path=f"/weather/pools/{pool_id}/window-summary",
            calling_service="communications",
            params={
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
            },
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

    return format_session_weather_summary(response.json())
