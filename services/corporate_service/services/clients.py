"""Outbound HTTP clients for cross-service orchestration.

The corporate_service NEVER reads other services' tables directly. All
fulfillment work (provisioning wallets, fetching cohort sessions, creating
bookings) goes through these typed helpers, which wrap the shared
``libs.common.service_client`` core with service-role JWTs.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from libs.common.config import get_settings
from libs.common.service_client.core import (
    internal_get,
    internal_post,
)

CALLER = "corporate_service"


# ---------------------------------------------------------------------------
# Members service
# ---------------------------------------------------------------------------


async def find_member_by_email(email: str) -> Optional[dict]:
    """Resolve a member by exact (case-insensitive) email.

    Uses the existing `/internal/members/search` endpoint and filters to an
    exact email match locally — search uses ILIKE substring, so we filter
    again here to be safe.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path="/internal/members/search",
        calling_service=CALLER,
        params={"q": email, "limit": 25},
    )
    resp.raise_for_status()
    needle = email.strip().lower()
    for row in resp.json():
        if (row.get("email") or "").lower() == needle:
            return row
    return None


# ---------------------------------------------------------------------------
# Wallet service
# ---------------------------------------------------------------------------


async def provision_corporate_wallet(
    *,
    program_id: UUID,
    company_name: str,
    company_email: str,
    admin_auth_id: str,
    budget_kobo: int,
    member_bubble_limit: Optional[int] = None,
) -> dict:
    """Create a CorporateWallet record in wallet_service.

    Returns the new wallet payload (including the corporate_wallet_id we
    store back on the CorporateProgram).
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.WALLET_SERVICE_URL,
        path="/internal/wallet/corporate/create",
        calling_service=CALLER,
        json={
            "corporate_program_id": str(program_id),
            "company_name": company_name,
            "company_email": company_email,
            "admin_auth_id": admin_auth_id,
            "budget_kobo": budget_kobo,
            "member_bubble_limit": member_bubble_limit,
        },
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Sessions service
# ---------------------------------------------------------------------------


async def get_cohort_session_ids(cohort_id: UUID) -> list[str]:
    """List session IDs that belong to a cohort."""
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.SESSIONS_SERVICE_URL,
        path=f"/internal/sessions/cohorts/{cohort_id}/session-ids",
        calling_service=CALLER,
    )
    resp.raise_for_status()
    return resp.json()


async def bulk_create_bookings(
    *,
    corporate_program_id: UUID,
    items: list[dict],
) -> dict:
    """Bulk-create CONFIRMED, channel=CORPORATE_BULK bookings.

    Each item must include: session_id, member_id, member_auth_id,
    fee_amount_kobo. The sessions endpoint is idempotent on
    (session_id, member_id) — duplicates are reported as ``skipped``.
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.SESSIONS_SERVICE_URL,
        path="/internal/sessions/bookings/bulk",
        calling_service=CALLER,
        json={
            "corporate_program_id": str(corporate_program_id),
            "items": items,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Academy service
# ---------------------------------------------------------------------------


async def get_cohort(cohort_id: UUID) -> Optional[dict]:
    """Look up a cohort to verify it exists before linking."""
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.ACADEMY_SERVICE_URL,
        path=f"/internal/academy/cohorts/{cohort_id}",
        calling_service=CALLER,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Attendance + Academy summaries (for outcome reports)
# ---------------------------------------------------------------------------


async def get_member_attendance_records(
    *, member_id: UUID, session_ids: list[str]
) -> list[dict]:
    """Get attendance records for a member filtered to a set of session IDs.

    Returns a list of ``{id, session_id, member_id, status}`` dicts. The
    status field is the AttendanceStatus enum value as a string (e.g.
    "present", "late", "absent"). Empty list if the member has no records.
    """
    if not session_ids:
        return []
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.ATTENDANCE_SERVICE_URL,
        path=f"/internal/attendance/member/{member_id}",
        calling_service=CALLER,
        params={"session_ids": ",".join(session_ids)},
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()


async def get_member_academy_summary(
    *, member_auth_id: str, date_from: str, date_to: str
) -> Optional[dict]:
    """Aggregate academy stats (milestones, certs) for a member in a window.

    Date params are ISO-8601 strings (with timezone). Returns dict with
    ``milestones_achieved``, ``milestones_in_progress``, ``programs_enrolled``,
    ``certificates_earned``. None if the academy service errors.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.ACADEMY_SERVICE_URL,
        path=f"/internal/academy/member-summary/{member_auth_id}",
        calling_service=CALLER,
        params={"from": date_from, "to": date_to},
        timeout=20.0,
    )
    if resp.status_code >= 400:
        return None
    return resp.json()
