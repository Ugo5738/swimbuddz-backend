"""High-level helpers for the members service.

Covers member lookups, coach profile/readiness, membership tiers, birthdays,
admin rosters, eligible coaches, and the pod helpers used by sessions_service.
"""

from __future__ import annotations

from typing import Any, Optional

from libs.common.config import get_settings

from .core import internal_get, internal_post


async def get_member_by_auth_id(
    auth_id: str, *, calling_service: str
) -> Optional[dict]:
    """Look up a member by their Supabase auth_id.

    Returns dict with {id, first_name, last_name, email} or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/internal/members/by-auth/{auth_id}",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def search_members(
    query: str, *, calling_service: str, limit: int = 50
) -> list[dict]:
    """Search members by first name, last name, or email.

    Returns list of dicts with {id, auth_id, first_name, last_name, email}.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path="/internal/members/search",
        calling_service=calling_service,
        params={"q": query, "limit": limit},
    )
    resp.raise_for_status()
    return resp.json()


async def get_member_by_id(member_id: str, *, calling_service: str) -> Optional[dict]:
    """Look up a member by their member ID.

    Returns dict with {id, auth_id, first_name, last_name, email, phone,
    community_paid_until, profile_photo_url} or None. `auth_id` is the
    Supabase user UUID — required to call members-service activation
    endpoints which key on auth_id (e.g. `/admin/members/by-auth/{auth_id}
    /academy/activate`).
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/internal/members/{member_id}",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_members_bulk(
    member_ids: list[str], *, calling_service: str
) -> list[dict]:
    """Bulk-lookup members by IDs.

    Returns list of {id, auth_id, first_name, last_name, email, phone,
    community_paid_until}.
    """
    if not member_ids:
        return []
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.MEMBERS_SERVICE_URL,
        path="/internal/members/bulk",
        calling_service=calling_service,
        json={"ids": member_ids},
    )
    resp.raise_for_status()
    return resp.json()


async def get_coach_profile(member_id: str, *, calling_service: str) -> Optional[dict]:
    """Look up coach profile by member_id.

    Returns dict with {member_id, status, academy_cohort_stipend, ...} or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/internal/members/coaches/{member_id}/profile",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_coach_availability(
    member_id: str, *, calling_service: str
) -> Optional[dict]:
    """Look up a coach's availability calendar + spacing override.

    Returns {member_id, availability_calendar, min_hours_between_sessions} or
    None if the member has no coach profile.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/internal/members/coaches/{member_id}/availability",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_member_membership(
    member_id: str, *, calling_service: str
) -> Optional[dict]:
    """Look up a member's membership tier and billing info.

    Returns dict with {member_id, primary_tier, active_tiers,
    community_paid_until, club_paid_until, academy_paid_until} or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/internal/members/{member_id}/membership",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_coach_readiness_data(
    member_id: str, *, calling_service: str
) -> Optional[dict]:
    """Get extended coach profile data for readiness assessment.

    Returns dict with {profile_id, total_coaching_hours, average_rating,
    background_check_status, has_cpr_training, cpr_expiry_date, has_active_agreement}
    or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/internal/members/coaches/{member_id}/readiness",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_birthdays_today(
    *,
    calling_service: str,
    on: Optional[str] = None,
) -> list[dict]:
    """Return active members whose date_of_birth falls on ``on`` (defaults to today in Lagos).

    Each item: {id, first_name, last_name, email, age}.
    """
    settings = get_settings()
    params: dict[str, Any] = {}
    if on:
        params["on"] = on
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path="/internal/members/birthdays-today",
        calling_service=calling_service,
        params=params or None,
    )
    resp.raise_for_status()
    return resp.json()


async def get_admin_members(*, calling_service: str) -> list[dict]:
    """Return active members with admin-flavoured roles.

    Each item: {id, first_name, last_name, email, roles}.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path="/internal/members/admins",
        calling_service=calling_service,
    )
    resp.raise_for_status()
    return resp.json()


async def get_eligible_coaches(
    grade_column: str,
    eligible_grades: list[str],
    *,
    calling_service: str,
) -> list[dict]:
    """Get eligible coaches filtered by grade column and allowed grades.

    Returns list of {member_id, name, email, grade, total_coaching_hours, average_feedback_rating}.
    """
    if not eligible_grades:
        return []
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path="/internal/members/coaches/eligible",
        calling_service=calling_service,
        params={
            "grade_column": grade_column,
            "eligible_grades": ",".join(eligible_grades),
        },
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Pod helpers (sessions_service ↔ pods integration)
# ---------------------------------------------------------------------------


async def get_pod_by_id(pod_id: str, *, calling_service: str) -> Optional[dict]:
    """Look up a single pod by id (with active member roster).

    Used by sessions_service when scheduling a Club session that's scoped
    to a specific pod — needs the pod's default schedule and member list.

    Returns a dict matching ``PodInternalDetail``:
        {
            id, club_id, name, slug, handle,
            pod_lead_id, assistant_pod_lead_id,
            status, visibility, min_size, max_size, active_member_count,
            default_session_day, default_session_time,
            default_session_duration_minutes, default_pool_id,
            active_member_ids: [...],
        }
    Or ``None`` if the pod doesn't exist.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/internal/members/pods/{pod_id}",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def list_pods(
    *,
    calling_service: str,
    club_id: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """List pods, optionally filtered by club and status.

    Used by sessions_service for batch scheduling — e.g. "create this
    Saturday's sessions for every active pod in club X". Defaults to
    ``status='active'`` server-side; pass ``status='all'`` to include
    dissolved pods.

    Returns a list of dicts matching ``PodInternalSummary`` (no
    ``active_member_ids`` — use :func:`get_pod_by_id` per pod when you
    need the roster).
    """
    settings = get_settings()
    params: dict = {}
    if club_id is not None:
        params["club_id"] = club_id
    if status is not None:
        params["status"] = status

    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path="/internal/members/pods",
        calling_service=calling_service,
        params=params or None,
    )
    resp.raise_for_status()
    return resp.json()
