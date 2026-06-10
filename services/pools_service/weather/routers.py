"""Weather read + admin endpoints, mounted inside pools_service.

Exposes ``/weather`` (member) and ``/admin/weather`` (admin). Hosted here
rather than as a standalone service because pools owns the coordinates the
forecast keys on — so the by-pool path reads the Pool table directly.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.pools_service.weather.models import WeatherSnapshot
from services.pools_service.weather.refresh import get_pool_coords, refresh_all_pools
from services.pools_service.weather.schemas import (
    WeatherRefreshResult,
    WeatherSnapshotResponse,
)
from services.pools_service.weather.snapshot_service import (
    get_or_fetch,
    get_snapshot_by_pool,
    is_stale,
    list_snapshots,
)

member_router = APIRouter(tags=["weather"])
admin_router = APIRouter(tags=["admin-weather"])


# ----------------------------------------------------------------------------
# Response helpers (date-slicing + computed `stale`) — importable for tests.
# ----------------------------------------------------------------------------
def slice_hourly(hourly: dict, date: str) -> dict:
    """Trim parallel hourly arrays to a single local day (YYYY-MM-DD)."""
    times = hourly.get("time") or []
    idx = [i for i, t in enumerate(times) if isinstance(t, str) and t.startswith(date)]
    if not idx:
        return hourly
    out: dict = {}
    for key, arr in hourly.items():
        if isinstance(arr, list) and len(arr) == len(times):
            out[key] = [arr[i] for i in idx]
        else:
            out[key] = arr
    return out


def slice_daily(daily: Optional[dict], date: str) -> Optional[dict]:
    """Trim parallel daily arrays to a single day (YYYY-MM-DD)."""
    if not daily:
        return daily
    times = daily.get("time") or []
    idx = [i for i, t in enumerate(times) if isinstance(t, str) and t.startswith(date)]
    if not idx:
        return daily
    out: dict = {}
    for key, arr in daily.items():
        if isinstance(arr, list) and len(arr) == len(times):
            out[key] = [arr[i] for i in idx]
        else:
            out[key] = arr
    return out


def to_response(
    snapshot: WeatherSnapshot, *, date: Optional[str] = None
) -> WeatherSnapshotResponse:
    """Build the API response, computing ``stale`` and optionally day-slicing."""
    resp = WeatherSnapshotResponse.model_validate(snapshot)
    resp.stale = is_stale(snapshot)
    if date:
        resp.hourly = slice_hourly(snapshot.hourly or {}, date)
        resp.daily = slice_daily(snapshot.daily, date)
    return resp


# ----------------------------------------------------------------------------
# Member endpoints — mounted at /weather
# ----------------------------------------------------------------------------
@member_router.get("", response_model=WeatherSnapshotResponse)
async def get_weather(
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude"),
    date: Optional[str] = Query(
        None,
        description="Optional YYYY-MM-DD to return only that day's hours",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
    _: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Cached forecast for any coordinates (cache-aside)."""
    snapshot = await get_or_fetch(db, latitude=lat, longitude=lon)
    return to_response(snapshot, date=date)


@member_router.get("/pools/{pool_id}", response_model=WeatherSnapshotResponse)
async def get_weather_for_pool(
    pool_id: uuid.UUID,
    date: Optional[str] = Query(
        None,
        description="Optional YYYY-MM-DD to return only that day's hours",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    ),
    _: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Cached forecast for a specific pool.

    Fast path: the pre-fetched snapshot. On a cache miss, resolve the pool's
    coordinates directly from the Pool table (same service, no HTTP).
    """
    snapshot = await get_snapshot_by_pool(db, pool_id)
    if snapshot is not None:
        snapshot = await get_or_fetch(
            db,
            latitude=snapshot.latitude,
            longitude=snapshot.longitude,
            pool_id=pool_id,
            label=snapshot.label,
        )
        return to_response(snapshot, date=date)

    coords = await get_pool_coords(db, pool_id)
    if coords is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No weather available for this pool (unknown or missing coordinates).",
        )
    lat, lon, label = coords
    snapshot = await get_or_fetch(
        db, latitude=lat, longitude=lon, pool_id=pool_id, label=label
    )
    return to_response(snapshot, date=date)


# ----------------------------------------------------------------------------
# Admin endpoints — mounted at /admin/weather
# ----------------------------------------------------------------------------
@admin_router.post("/refresh", response_model=WeatherRefreshResult)
async def trigger_refresh(
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Force a synchronous pre-fetch of every active pool's forecast."""
    result = await refresh_all_pools(db)
    return WeatherRefreshResult(**result)


@admin_router.get("/snapshots", response_model=list[WeatherSnapshotResponse])
async def list_weather_snapshots(
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List cached snapshots (debug/health view of what's been pre-fetched)."""
    snapshots = await list_snapshots(db)
    return [to_response(s) for s in snapshots]
