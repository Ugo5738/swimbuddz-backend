"""Unit tests for weather caching/slicing logic (no DB, no network)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from libs.common.datetime_utils import utc_now
from services.pools_service.weather.models import WeatherSnapshot
from services.pools_service.weather.routers import (
    slice_daily,
    slice_hourly,
    to_response,
    to_window_summary_response,
)
from services.pools_service.weather.snapshot_service import (
    is_stale,
    normalize_location_key,
)
from services.pools_service.weather.summary import (
    classify_weather,
    condition_label,
    summarize_weather_window,
    weather_explanation,
)

_HOURLY = {
    "time": ["2026-06-06T22:00", "2026-06-06T23:00", "2026-06-07T00:00"],
    "precipitation_probability": [76, 53, 41],
    "precipitation": [0.2, 0.0, 0.1],
    "temperature_2m": [27.0, 26.5, 26.0],
    "weather_code": [63, 61, 3],
}
_DAILY = {
    "time": ["2026-06-06", "2026-06-07"],
    "precipitation_sum": [13.0, 16.2],
}


def _snapshot(**overrides) -> WeatherSnapshot:
    now = utc_now()
    defaults = dict(
        id=uuid.uuid4(),
        location_key="6.51,3.37",
        latitude=6.5095,
        longitude=3.3711,
        pool_id=None,
        label="Yaba",
        provider="open-meteo",
        timezone="Africa/Lagos",
        forecast_days=14,
        hourly=_HOURLY,
        daily=_DAILY,
        fetched_at=now,
        expires_at=now + timedelta(hours=3),
    )
    defaults.update(overrides)
    return WeatherSnapshot(**defaults)


def test_normalize_location_key_rounds_to_two_dp():
    assert normalize_location_key(6.5095, 3.3711) == "6.51,3.37"
    assert normalize_location_key(6.50, 3.37) == "6.50,3.37"


def test_normalize_location_key_dedupes_nearby_points():
    assert normalize_location_key(6.5095, 3.3711) == normalize_location_key(
        6.5142, 3.3688
    )


def test_is_stale_reflects_expiry():
    fresh = _snapshot(expires_at=utc_now() + timedelta(hours=1))
    stale = _snapshot(expires_at=utc_now() - timedelta(minutes=1))
    assert is_stale(fresh) is False
    assert is_stale(stale) is True


def test_slice_hourly_trims_to_one_day():
    sliced = slice_hourly(_HOURLY, "2026-06-06")
    assert sliced["time"] == ["2026-06-06T22:00", "2026-06-06T23:00"]
    assert sliced["precipitation_probability"] == [76, 53]


def test_slice_hourly_no_match_returns_input_unchanged():
    assert slice_hourly(_HOURLY, "2026-12-25") == _HOURLY


def test_slice_daily_trims_to_one_day():
    sliced = slice_daily(_DAILY, "2026-06-07")
    assert sliced["time"] == ["2026-06-07"]
    assert sliced["precipitation_sum"] == [16.2]


def test_to_response_computes_stale_and_slices():
    snap = _snapshot(expires_at=utc_now() - timedelta(minutes=1))
    resp = to_response(snap, date="2026-06-06")
    assert resp.stale is True
    assert resp.hourly["time"] == ["2026-06-06T22:00", "2026-06-06T23:00"]
    assert resp.label == "Yaba"


@pytest.mark.parametrize(
    ("code", "max_probability", "expected"),
    [
        (0, 0, "clear"),
        (2, 10, "partly"),
        (3, 35, "cloudy"),
        (61, 20, "rain"),
        (95, 10, "storm"),
    ],
)
def test_classify_weather_is_canonical(code, max_probability, expected):
    assert classify_weather(code, max_probability) == expected


def test_condition_label_and_explanation_have_safe_fallbacks():
    assert condition_label(53) == "Drizzle"
    assert condition_label(999) == "Cloudy"
    assert weather_explanation("clear", 10, 0) == "Looks dry for your session."


def test_summarize_weather_window_owns_shared_classification_and_copy():
    summary = summarize_weather_window(
        {
            "time": [
                "2026-07-18T10:00",
                "2026-07-18T11:00",
                "2026-07-18T12:00",
                "2026-07-18T13:00",
                "2026-07-18T14:00",
            ],
            "precipitation_probability": [90, 20, 65, 40, 10],
            "precipitation": [5.0, 0.0, 0.7, 0.5, 0.0],
            "temperature_2m": [26.0, 27.0, 28.4, 29.0, 25.0],
            "weather_code": [65, 3, 63, 61, 1],
        },
        starts_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
        timezone="Africa/Lagos",
    )

    assert summary is not None
    assert summary.window_start.isoformat() == "2026-07-18T11:00:00+01:00"
    assert summary.window_end.isoformat() == "2026-07-18T13:00:00+01:00"
    assert summary.max_precipitation_probability == 65
    assert summary.total_precipitation_mm == 1.2
    assert summary.temperature_high_c == 29.0
    assert summary.temperature_low_c == 27.0
    assert summary.representative_weather_code == 63
    assert summary.kind == "rain"
    assert summary.condition_text == "Rain"
    assert summary.explanation == "Light rain likely — warm and swimmable."


def test_summarize_weather_window_supports_windows_crossing_midnight():
    summary = summarize_weather_window(
        _HOURLY,
        starts_at=datetime(2026, 6, 6, 22, 30),
        ends_at=datetime(2026, 6, 7, 0, 15),
        timezone="Africa/Lagos",
    )

    assert summary is not None
    assert summary.max_precipitation_probability == 76
    assert summary.total_precipitation_mm == 0.3
    assert summary.temperature_low_c == 26.0


def test_summarize_weather_window_returns_none_without_matching_hours():
    assert (
        summarize_weather_window(
            _HOURLY,
            starts_at=datetime(2026, 12, 25, 10, 0),
            ends_at=datetime(2026, 12, 25, 12, 0),
            timezone="Africa/Lagos",
        )
        is None
    )


def test_summarize_weather_window_rejects_reversed_window():
    with pytest.raises(ValueError, match="ends_at"):
        summarize_weather_window(
            _HOURLY,
            starts_at=datetime(2026, 6, 7, 0, 45),
            ends_at=datetime(2026, 6, 7, 0, 15),
            timezone="Africa/Lagos",
        )


def test_to_window_summary_response_adds_snapshot_metadata():
    snap = _snapshot(expires_at=utc_now() - timedelta(minutes=1))
    response = to_window_summary_response(
        snap,
        starts_at=datetime(2026, 6, 6, 22, 0),
        ends_at=datetime(2026, 6, 6, 23, 0),
    )

    assert response is not None
    assert response.timezone == "Africa/Lagos"
    assert response.forecast_days == 14
    assert response.stale is True
    assert response.kind == "rain"
