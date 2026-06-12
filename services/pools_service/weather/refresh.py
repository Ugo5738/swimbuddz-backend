"""Pre-fetch orchestration — reads the Pool table directly (same service).

This is the payoff of hosting weather inside pools_service: the worker queries
``Pool`` in-process instead of making an HTTP call to fetch coordinates.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.logging import get_logger
from services.pools_service.models import Pool
from services.pools_service.weather.snapshot_service import fetch_and_store

logger = get_logger(__name__)


def _active_pools_query():
    # Any active pool that has been geocoded — partnership status is NOT a gate.
    # Weather is operational: we run sessions at "prospect" pools (e.g. Rowe Park)
    # too, so geocoding a pool (setting lat/long) is the opt-in, not the CRM stage.
    return select(Pool).where(
        Pool.is_active.is_(True),
        Pool.latitude.is_not(None),
        Pool.longitude.is_not(None),
    )


async def refresh_all_pools(db: AsyncSession) -> dict:
    """Pre-fetch and cache a forecast for every active pool with coordinates."""
    result = await db.execute(_active_pools_query())
    pools = result.scalars().all()

    refreshed = 0
    failed = 0
    skipped_no_coords = 0
    pool_ids: list[str] = []

    for pool in pools:
        if pool.latitude is None or pool.longitude is None:
            skipped_no_coords += 1
            continue
        try:
            await fetch_and_store(
                db,
                latitude=pool.latitude,
                longitude=pool.longitude,
                pool_id=pool.id,
                label=pool.location_area or pool.name,
            )
            refreshed += 1
            pool_ids.append(str(pool.id))
        except Exception as exc:  # noqa: BLE001 — one bad pool shouldn't abort the run
            failed += 1
            logger.warning("weather.refresh_failed pool=%s err=%s", pool.id, exc)

    logger.info(
        "weather.refresh complete: refreshed=%s failed=%s skipped_no_coords=%s",
        refreshed,
        failed,
        skipped_no_coords,
    )
    return {
        "refreshed": refreshed,
        "failed": failed,
        "skipped_no_coords": skipped_no_coords,
        "pool_ids": pool_ids,
    }


async def get_pool_coords(
    db: AsyncSession, pool_id: uuid.UUID
) -> Optional[tuple[float, float, Optional[str]]]:
    """Return (lat, lon, label) for an active geocoded pool, else None.

    Not gated on partnership status — any active pool with coordinates is
    eligible for weather (see ``_active_pools_query``).
    """
    result = await db.execute(
        select(Pool).where(
            Pool.id == pool_id,
            Pool.is_active.is_(True),
        )
    )
    pool = result.scalar_one_or_none()
    if pool is None or pool.latitude is None or pool.longitude is None:
        return None
    return (pool.latitude, pool.longitude, pool.location_area or pool.name)
