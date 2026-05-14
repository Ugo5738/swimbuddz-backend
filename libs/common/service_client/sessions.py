"""High-level helpers for the sessions service."""

from __future__ import annotations

from typing import Optional

from libs.common.config import get_settings

from .core import internal_get


async def get_session_by_id(session_id: str, *, calling_service: str) -> Optional[dict]:
    """Look up a session by ID.

    Returns dict with session details or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.SESSIONS_SERVICE_URL,
        path=f"/internal/sessions/{session_id}",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_next_session_for_cohort(
    cohort_id: str, *, calling_service: str
) -> Optional[dict]:
    """Get the next upcoming session for a cohort.

    Returns dict with {starts_at, title, location_name} or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.SESSIONS_SERVICE_URL,
        path=f"/internal/sessions/cohorts/{cohort_id}/next-session",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_session_ids_for_cohort(
    cohort_id: str, *, calling_service: str
) -> list[str]:
    """Get all session IDs for a cohort.

    Returns list of session ID strings.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.SESSIONS_SERVICE_URL,
        path=f"/internal/sessions/cohorts/{cohort_id}/session-ids",
        calling_service=calling_service,
    )
    resp.raise_for_status()
    return resp.json()
