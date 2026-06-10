"""Unit tests for the weather provider layer (no network)."""

import asyncio

import services.pools_service.weather.provider as prov
from services.pools_service.weather.provider import (
    OpenMeteoProvider,
    parse_open_meteo,
)

SAMPLE_OPEN_METEO = {
    "timezone": "Africa/Lagos",
    "hourly": {
        "time": ["2026-06-06T00:00", "2026-06-06T01:00", "2026-06-07T00:00"],
        "precipitation_probability": [40, 86, 30],
        "precipitation": [0.4, 4.0, 0.1],
        "temperature_2m": [26.0, 25.8, 26.2],
        "weather_code": [61, 63, 61],
    },
    "daily": {
        "time": ["2026-06-06", "2026-06-07"],
        "precipitation_sum": [13.0, 16.2],
        "precipitation_probability_max": [100, 100],
        "temperature_2m_max": [29.0, 27.0],
        "temperature_2m_min": [26.0, 25.0],
        "weather_code": [63, 65],
    },
}


def test_parse_open_meteo_extracts_blocks():
    result = parse_open_meteo(SAMPLE_OPEN_METEO)
    assert result.provider == "open-meteo"
    assert result.timezone == "Africa/Lagos"
    assert result.hourly["precipitation_probability"] == [40, 86, 30]
    assert result.daily["precipitation_sum"] == [13.0, 16.2]


def test_parse_open_meteo_handles_missing_fields():
    result = parse_open_meteo({}, fallback_timezone="Africa/Lagos")
    assert result.hourly == {}
    assert result.daily is None
    assert result.timezone == "Africa/Lagos"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    captured: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        _FakeClient.captured = {"url": url, "params": params or {}}
        return _FakeResp(SAMPLE_OPEN_METEO)


def test_fetch_forecast_builds_request_and_parses(monkeypatch):
    monkeypatch.setattr(prov.httpx, "AsyncClient", _FakeClient)
    provider = OpenMeteoProvider()

    result = asyncio.run(
        provider.fetch_forecast(
            latitude=6.5095, longitude=3.3711, days=14, timezone="Africa/Lagos"
        )
    )

    params = _FakeClient.captured["params"]
    assert params["latitude"] == 6.5095
    assert params["longitude"] == 3.3711
    assert params["forecast_days"] == 14
    assert "precipitation_probability" in params["hourly"]
    assert result.hourly["temperature_2m"] == [26.0, 25.8, 26.2]


def test_fetch_forecast_clamps_days_to_provider_max(monkeypatch):
    monkeypatch.setattr(prov.httpx, "AsyncClient", _FakeClient)
    provider = OpenMeteoProvider()

    asyncio.run(
        provider.fetch_forecast(latitude=1.0, longitude=2.0, days=30, timezone="UTC")
    )
    assert _FakeClient.captured["params"]["forecast_days"] == 16
