"""High-level helpers for the academy service."""

from __future__ import annotations

from typing import Optional

from libs.common.config import get_settings

from .core import internal_get


async def check_cohort_enrollment(
    cohort_id: str, member_id: str, *, calling_service: str
) -> Optional[dict]:
    """Check if a member is enrolled in a specific academy cohort.

    Returns dict with {enrolled: bool, status: str|None, access_suspended: bool}.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.ACADEMY_SERVICE_URL,
        path=f"/internal/academy/cohorts/{cohort_id}/check-enrollment/{member_id}",
        calling_service=calling_service,
    )
    resp.raise_for_status()
    return resp.json()
