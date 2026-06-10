"""Snapshot storage + cache-aside logic for the weather module."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from services.pools_service.weather.models import WeatherSnapshot
from services.pools_service.weather.provider import ForecastData, get_provider

logger = get_logger(__name__)


def normalize_location_key(latitude: float, longitude: float) -> str:
    """Round coords to ~1km so nearby requests share one cache row."""
    return f"{round(float(latitude), 2):.2f},{round(float(longitude), 2):.2f}"


def _ttl_minutes() -> int:
    return int(get_settings().WEATHER_CACHE_TTL_MINUTES or 180)


def _forecast_days() -> int:
    return int(get_settings().WEATHER_FORECAST_DAYS or 14)


def _timezone() -> str:
    return get_settings().TIMEZONE or "Africa/Lagos"


def is_stale(snapshot: WeatherSnapshot) -> bool:
    """True when the snapshot is past its TTL and due for a refetch."""
    return snapshot.expires_at <= utc_now()


async def get_snapshot_by_key(
    db: AsyncSession, location_key: str
) -> Optional[WeatherSnapshot]:
    result = await db.execute(
        select(WeatherSnapshot).where(WeatherSnapshot.location_key == location_key)
    )
    return result.scalar_one_or_none()


async def get_snapshot_by_pool(
    db: AsyncSession, pool_id: uuid.UUID
) -> Optional[WeatherSnapshot]:
    result = await db.execute(
        select(WeatherSnapshot)
        .where(WeatherSnapshot.pool_id == pool_id)
        .order_by(WeatherSnapshot.fetched_at.desc())
    )
    return result.scalars().first()


async def list_snapshots(db: AsyncSession, limit: int = 200) -> list[WeatherSnapshot]:
    result = await db.execute(
        select(WeatherSnapshot).order_by(WeatherSnapshot.fetched_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def store_forecast(
    db: AsyncSession,
    *,
    latitude: float,
    longitude: float,
    forecast: ForecastData,
    pool_id: Optional[uuid.UUID] = None,
    label: Optional[str] = None,
    days: Optional[int] = None,
    ttl_minutes: Optional[int] = None,
) -> WeatherSnapshot:
    """Upsert a forecast keyed by normalized location."""
    days = days or _forecast_days()
    ttl = ttl_minutes if ttl_minutes is not None else _ttl_minutes()
    now = utc_now()
    location_key = normalize_location_key(latitude, longitude)

    snapshot = await get_snapshot_by_key(db, location_key)
    if snapshot is None:
        snapshot = WeatherSnapshot(location_key=location_key)
        db.add(snapshot)

    snapshot.latitude = float(latitude)
    snapshot.longitude = float(longitude)
    snapshot.provider = forecast.provider
    snapshot.timezone = forecast.timezone
    snapshot.forecast_days = days
    snapshot.hourly = forecast.hourly
    snapshot.daily = forecast.daily
    snapshot.fetched_at = now
    snapshot.expires_at = now + timedelta(minutes=ttl)
    if pool_id is not None:
        snapshot.pool_id = pool_id
    if label is not None:
        snapshot.label = label

    await db.commit()
    await db.refresh(snapshot)
    return snapshot


async def fetch_and_store(
    db: AsyncSession,
    *,
    latitude: float,
    longitude: float,
    pool_id: Optional[uuid.UUID] = None,
    label: Optional[str] = None,
) -> WeatherSnapshot:
    """Pull a fresh forecast from the provider and persist it."""
    provider = get_provider()
    forecast = await provider.fetch_forecast(
        latitude=latitude,
        longitude=longitude,
        days=_forecast_days(),
        timezone=_timezone(),
    )
    return await store_forecast(
        db,
        latitude=latitude,
        longitude=longitude,
        forecast=forecast,
        pool_id=pool_id,
        label=label,
    )


async def get_or_fetch(
    db: AsyncSession,
    *,
    latitude: float,
    longitude: float,
    pool_id: Optional[uuid.UUID] = None,
    label: Optional[str] = None,
    force: bool = False,
) -> WeatherSnapshot:
    """Cache-aside read: serve a fresh cached row, else fetch + store.

    On provider failure with a stale row present, the stale row is returned
    rather than raising — weather is better-late-than-never for planning.
    """
    location_key = normalize_location_key(latitude, longitude)
    snapshot = await get_snapshot_by_key(db, location_key)
    if snapshot is not None and not force and not is_stale(snapshot):
        return snapshot

    try:
        return await fetch_and_store(
            db, latitude=latitude, longitude=longitude, pool_id=pool_id, label=label
        )
    except Exception as exc:  # noqa: BLE001 — degrade gracefully on provider error
        logger.warning("weather.fetch_failed key=%s err=%s", location_key, exc)
        if snapshot is not None:
            return snapshot
        raise
