"""High-level helpers for the Events service's Session contract."""

from __future__ import annotations

from typing import Any

from libs.common.config import get_settings

from .core import internal_get, internal_post


async def get_event_session_contract(
    event_id: str, *, calling_service: str
) -> dict[str, Any] | None:
    """Return Event-owned fields and attendance policy for a linked Session."""
    settings = get_settings()
    response = await internal_get(
        service_url=settings.EVENTS_SERVICE_URL,
        path=f"/internal/events/{event_id}/session-contract",
        calling_service=calling_service,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


async def check_event_attendance_batch(
    *,
    checks: list[dict[str, Any]],
    member_id: str,
    calling_service: str,
) -> dict[str, dict[str, Any]]:
    """Ask Events to evaluate attendance for several linked Event Sessions."""
    if not checks:
        return {}
    settings = get_settings()
    response = await internal_post(
        service_url=settings.EVENTS_SERVICE_URL,
        path="/internal/events/attendance/checks",
        calling_service=calling_service,
        json={
            "checks": checks,
            "member_id": member_id,
        },
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}
