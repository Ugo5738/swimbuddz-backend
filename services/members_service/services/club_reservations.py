"""Fulfill prepaid enrollments via Sessions; transition access is opt-in."""

from fastapi import HTTPException

from libs.common.config import get_settings
from libs.common.service_client import internal_post


async def reserve_enrollment_swims(enrollment, plan, member) -> None:
    if (
        enrollment.payment_mode != "quarterly_prepaid"
        or enrollment.status != "active"
        or not plan.session_links
    ):
        return
    response = await internal_post(
        service_url=get_settings().SESSIONS_SERVICE_URL,
        path="/internal/sessions/club-reservations",
        calling_service="members",
        json={
            "enrollment_id": str(enrollment.id),
            "club_id": str(enrollment.club_id),
            "member_id": str(member.id),
            "member_auth_id": member.auth_id,
            "starts_at": enrollment.starts_at.isoformat(),
            "ends_at": enrollment.ends_at.isoformat(),
            "session_ids": [str(link.session_id) for link in plan.session_links],
        },
        timeout=90,
    )
    if response.status_code >= 400:
        raise HTTPException(
            502,
            "Club payment is recorded, but swim reservations need retry/reconciliation",
        )
