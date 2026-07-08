"""High-level helpers for the transport service."""

from __future__ import annotations

from typing import Any

from libs.common.config import get_settings

from .core import internal_post


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
