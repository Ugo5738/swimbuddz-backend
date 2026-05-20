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


async def list_enrollment_progress(
    enrollment_id: str, *, calling_service: str
) -> list[dict]:
    """List all StudentProgress rows for an enrollment.

    Used by media_service to find which media items belong to which
    milestone claim when assembling the admin evidence gallery — keeps
    the cross-service join out of the database (per the service
    isolation rule in CONVENTIONS.md §2) by going through the HTTP
    API instead.

    Returns a list of dicts mirroring ``StudentProgressResponse``
    (id, enrollment_id, milestone_id, status, evidence_media_id,
    student_notes, achieved_at, …). Returns ``[]`` if the enrollment
    has no progress rows yet; raises on transport / server errors.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.ACADEMY_SERVICE_URL,
        path=f"/academy/enrollments/{enrollment_id}/progress",
        calling_service=calling_service,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []
