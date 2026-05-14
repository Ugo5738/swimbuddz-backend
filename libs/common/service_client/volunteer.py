"""High-level helpers for the volunteer service."""

from __future__ import annotations

from typing import Optional

from libs.common.config import get_settings

from .core import internal_post


async def grant_challenge_volunteer_hours(
    *,
    member_id: str,
    hours: float,
    submission_id: str,
    logged_by: Optional[str],
    notes: Optional[str],
    calling_service: str,
) -> dict:
    """Credit volunteer hours to a member for an approved challenge submission.

    Called by members_service after admin approves a submission. Idempotent
    via (source='challenge_completion', external_reference_id=submission_id,
    member_id) tuple enforced by a partial unique index on the
    volunteer_hours_log table.
    Returns the LogHoursResponse dict from volunteer_service.
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.VOLUNTEER_SERVICE_URL,
        path="/internal/volunteer/log-hours",
        calling_service=calling_service,
        json={
            "member_id": member_id,
            "hours": hours,
            "source": "challenge_completion",
            "external_reference_id": submission_id,
            "logged_by": logged_by,
            "notes": notes,
        },
    )
    resp.raise_for_status()
    return resp.json()
