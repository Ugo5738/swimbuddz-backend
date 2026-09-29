"""Fulfill prepaid enrollments via Sessions; transition access is opt-in."""

from fastapi import HTTPException

from libs.common.config import get_settings
from libs.common.service_client import internal_post


async def reserve_enrollment_swims(
    enrollment, plan, member, *, require_holds=False
) -> None:
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
            "payment_reference": getattr(enrollment, "payment_reference", None),
            "require_holds": require_holds,
        },
        timeout=90,
    )
    if response.status_code >= 400:
        raise HTTPException(
            502,
            "Club payment is recorded, but swim reservations need retry/reconciliation",
        )


async def session_hold_request(path, payload):
    response = await internal_post(
        service_url=get_settings().SESSIONS_SERVICE_URL,
        path=f"/internal/sessions/club-holds{path}",
        calling_service="members",
        json=payload,
        timeout=90,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise HTTPException(
            response.status_code if response.status_code in {400, 409, 422} else 502,
            detail or "Could not reserve the included Club swim seats",
        )
    return response.json()


async def hold_application_swims(application, plans, reference, expires_at):
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo

    if any(not plan.session_links for plan in plans):
        raise HTTPException(
            409, "Generate and publish the exact quarter swims before selling this plan"
        )
    tz = ZoneInfo("Africa/Lagos")
    return await session_hold_request(
        "",
        {
            "application_id": str(application.id),
            "club_id": str(application.club_id),
            "member_id": str(application.member_id),
            "payment_reference": reference,
            "expires_at": expires_at.isoformat(),
            "plans": [
                {
                    "plan_version_id": str(plan.id),
                    "starts_at": datetime.combine(
                        plan.period_start, time.min, tzinfo=tz
                    ).isoformat(),
                    "ends_at": datetime.combine(
                        plan.period_end + timedelta(days=1), time.min, tzinfo=tz
                    ).isoformat(),
                    "session_ids": [
                        str(link.session_id) for link in plan.session_links
                    ],
                }
                for plan in plans
            ],
        },
    )
