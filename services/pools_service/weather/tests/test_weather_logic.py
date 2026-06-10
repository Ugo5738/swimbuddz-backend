"""Unit tests for weather caching/slicing logic (no DB, no network)."""

import uuid
from datetime import timedelta

from libs.common.datetime_utils import utc_now
from services.pools_service.weather.models import WeatherSnapshot
from services.pools_service.weather.routers import (
    slice_daily,
    slice_hourly,
    to_response,
)
from services.pools_service.weather.snapshot_service import (
    is_stale,
    normalize_location_key,
)

_HOURLY = {
    "time": ["2026-06-06T22:00", "2026-06-06T23:00", "2026-06-07T00:00"],
    "precipitation_probability": [76, 53, 41],
    "precipitation": [0.2, 0.0, 0.1],
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
