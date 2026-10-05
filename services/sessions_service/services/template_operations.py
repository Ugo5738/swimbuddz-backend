"""Operational inheritance shared by ordinary and quarterly generation."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from libs.common.currency import naira_to_kobo
from libs.common.config import get_settings
from libs.common.service_client import (
    attach_session_ride_configs,
    internal_get,
    materialise_opportunities_from_session_template,
)
from libs.common.service_client.pools import get_partner_pool
from services.sessions_service.schemas.template_admission import (
    TemplateAdmissionSettings,
)


async def template_volunteer_slots(template_id) -> list[dict]:
    response = await internal_get(
        service_url=get_settings().VOLUNTEER_SERVICE_URL,
        path=f"/admin/volunteers/session-templates/{template_id}/slots",
        calling_service="sessions",
    )
    response.raise_for_status()
    return sorted(response.json(), key=lambda slot: slot["id"])


async def template_location(template) -> dict:
    if template.pool_id:
        pool = await get_partner_pool(str(template.pool_id), calling_service="sessions")
        if not pool or not pool.get("name"):
            raise HTTPException(422, "Choose an active pool with a venue name first")
        return {"location_name": pool["name"], "location_address": pool.get("address")}
    return {"location_name": template.location_name or template.location}


def template_admission(template, starts_at) -> dict:
    settings = TemplateAdmissionSettings.model_validate(
        getattr(template, "admission_settings", None) or {}
    )
    values = settings.model_dump(
        exclude={
            "guest_fee",
            "community_dropin_fee",
            "visiting_club_fee",
            "guest_booking_cutoff_hours",
        }
    )
    values.update(
        guest_fee_kobo=naira_to_kobo(settings.guest_fee)
        if settings.guest_fee is not None
        else None,
        community_dropin_fee_kobo=naira_to_kobo(settings.community_dropin_fee)
        if settings.community_dropin_fee is not None
        else None,
        visiting_club_fee_kobo=naira_to_kobo(settings.visiting_club_fee)
        if settings.visiting_club_fee is not None
        else None,
        guest_booking_closes_at=starts_at
        - timedelta(hours=settings.guest_booking_cutoff_hours),
    )
    return values


async def materialise_template_operations(template, session) -> None:
    """Idempotent calls after commit; failure propagates so retry repairs it."""
    tz = ZoneInfo(session.timezone or "Africa/Lagos")
    starts, ends = session.starts_at.astimezone(tz), session.ends_at.astimezone(tz)
    await materialise_opportunities_from_session_template(
        calling_service="sessions",
        session_id=str(session.id),
        session_template_id=str(template.id),
        date=starts.date().isoformat(),
        start_time=starts.time().isoformat(),
        end_time=ends.time().isoformat(),
        location_name=session.location_name,
    )
    if template.ride_share_config:
        await attach_session_ride_configs(
            session_id=str(session.id),
            configs=template.ride_share_config,
            calling_service="sessions",
            preserve_existing=True,
        )
