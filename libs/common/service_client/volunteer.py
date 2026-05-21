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


async def materialise_opportunities_from_session_template(
    *,
    calling_service: str,
    session_id: str,
    session_template_id: str,
    date: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    location_name: Optional[str] = None,
) -> dict:
    """Tell volunteer_service to fan-out opportunities for a newly-generated session.

    Called by sessions_service after committing a session generated from a
    template. Volunteer service reads its own ``SessionTemplateVolunteerSlot``
    rows for that template and creates one ``VolunteerOpportunity`` per
    active slot. Idempotent on the volunteer side. Best-effort: callers
    should treat HTTP failures as non-fatal.
    Returns the materialise response dict from volunteer_service.
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.VOLUNTEER_SERVICE_URL,
        path="/internal/volunteer/opportunities/from-session-template",
        calling_service=calling_service,
        json={
            "session_id": session_id,
            "session_template_id": session_template_id,
            "date": date,
            "start_time": start_time,
            "end_time": end_time,
            "location_name": location_name,
        },
    )
    resp.raise_for_status()
    return resp.json()


async def cancel_opportunities_for_context(
    *,
    calling_service: str,
    session_id: Optional[str] = None,
    event_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    """Cascade-cancel volunteer opportunities tied to a cancelled session/event.

    Exactly one of ``session_id`` / ``event_id`` must be set. Already-cancelled
    or completed opportunities are skipped on the volunteer side, so this is
    safe to retry. Best-effort: callers should treat HTTP failures as
    non-fatal (the session cancellation itself has already committed).
    Returns the CancelByContextResponse dict from volunteer_service.
    """
    if (session_id is None) == (event_id is None):
        raise ValueError(
            "cancel_opportunities_for_context: exactly one of session_id/event_id must be set"
        )
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.VOLUNTEER_SERVICE_URL,
        path="/internal/volunteer/opportunities/cancel-for-context",
        calling_service=calling_service,
        json={
            "session_id": session_id,
            "event_id": event_id,
            "reason": reason,
        },
    )
    resp.raise_for_status()
    return resp.json()
