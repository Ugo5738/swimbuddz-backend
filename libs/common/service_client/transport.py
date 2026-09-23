"""High-level helpers for the transport service."""

from __future__ import annotations

from typing import Any

from libs.common.config import get_settings

from .core import internal_patch, internal_post


async def attach_session_ride_configs(
    *,
    session_id: str,
    configs: list[dict[str, Any]],
    calling_service: str,
) -> dict[str, Any]:
    """Attach/replace ride-share configs for a session via service-role auth."""
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.TRANSPORT_SERVICE_URL,
        path=f"/internal/transport/sessions/{session_id}/ride-configs",
        calling_service=calling_service,
        json=configs,
    )
    resp.raise_for_status()
    return resp.json()


async def reconcile_session_ride_schedule(
    *,
    session_id: str,
    old_starts_at: str,
    new_starts_at: str,
    calling_service: str,
) -> dict[str, Any]:
    """Shift explicit ride departures by the Session schedule delta."""
    settings = get_settings()
    resp = await internal_patch(
        service_url=settings.TRANSPORT_SERVICE_URL,
        path=f"/internal/transport/sessions/{session_id}/schedule",
        calling_service=calling_service,
        json={
            "old_starts_at": old_starts_at,
            "new_starts_at": new_starts_at,
        },
    )
    resp.raise_for_status()
    return resp.json()
