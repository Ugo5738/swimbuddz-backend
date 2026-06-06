"""High-level helpers for attendance_service."""

from __future__ import annotations

from libs.common.config import get_settings

from .core import internal_get


async def get_member_attendance(
    member_id: str,
    *,
    session_ids: list[str] | None = None,
    calling_service: str,
) -> list[dict]:
    """Attendance records for a member, optionally filtered to ``session_ids``.

    Each item: ``{id, session_id, member_id, status}`` with ``status`` lowercased
    (e.g. 'present' / 'late' / 'absent'). Returns an empty list on 404 / none.
    """
    settings = get_settings()
    path = f"/internal/attendance/member/{member_id}"
    if session_ids:
        path += "?session_ids=" + ",".join(session_ids)
    resp = await internal_get(
        service_url=settings.ATTENDANCE_SERVICE_URL,
        path=path,
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json()
