"""Service-client helpers for talking to pools_service."""

from __future__ import annotations

from typing import Optional

from libs.common.config import get_settings
from libs.common.service_client.core import internal_get


async def get_partner_pool(pool_id: str, *, calling_service: str) -> Optional[dict]:
    """Fetch an *active-partner* pool by id from pools_service.

    Hits the public ``GET /pools/{pool_id}`` route, which only returns pools
    with ``partnership_status == ACTIVE_PARTNER`` and ``is_active``. Returns the
    PoolResponse dict (includes ``price_per_swimmer_ngn``, ``flat_session_fee_ngn``,
    ``name``, ``max_swimmers_capacity``, …) or ``None`` if the pool does not exist
    or is not a bookable active partner.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.POOLS_SERVICE_URL,
        path=f"/pools/{pool_id}",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()
