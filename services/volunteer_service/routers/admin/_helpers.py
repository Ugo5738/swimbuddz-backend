"""Shared helpers for the admin volunteer router submodules."""

import logging
from datetime import datetime, timezone

from libs.common.member_utils import resolve_member_basic
from libs.common.service_client import emit_rewards_event, get_member_by_id
from services.volunteer_service.models import (
    SlotStatus,
    VolunteerHoursLog,
    VolunteerOpportunity,
    VolunteerRoleCategory,
    VolunteerSlot,
)
from services.volunteer_service.services import update_profile_aggregates
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _is_peer_coaching(opp: VolunteerOpportunity | None) -> bool:
    """Check if an opportunity is a peer coaching session."""
    if opp and opp.role:
        return opp.role.category == VolunteerRoleCategory.MENTOR
    return False


async def _emit_volunteer_reward(
    slot: VolunteerSlot,
    opp: VolunteerOpportunity | None,
) -> None:
    """Best-effort: emit a rewards event for a completed volunteer slot."""
    try:
        member = await get_member_by_id(
            str(slot.member_id), calling_service="volunteer"
        )
        if not member:
            logger.warning(
                "Could not look up member %s for rewards event", slot.member_id
            )
            return

        event_type = (
            "volunteer.peer_coaching"
            if _is_peer_coaching(opp)
            else "volunteer.completed"
        )
        await emit_rewards_event(
            event_type=event_type,
            member_auth_id=member["auth_id"],
            member_id=str(slot.member_id),
            service_source="volunteer",
            event_data={
                "hours": slot.hours_logged,
                "role": opp.title if opp else "unknown",
                "event_name": opp.title if opp else "Volunteer session",
                "admin_confirmed": True,
            },
            idempotency_key=f"vol-checkout-{slot.id}",
            calling_service="volunteer",
        )
    except Exception:
        logger.warning(
            "Failed to emit rewards event for slot %s", slot.id, exc_info=True
        )


async def _enrich_opportunity(opp: VolunteerOpportunity) -> dict:
    data = {c.key: getattr(opp, c.key) for c in opp.__table__.columns}
    data["role_title"] = opp.role.title if opp.role else None
    data["role_category"] = opp.role.category.value if opp.role else None
    return data


async def _enrich_slot(slot: VolunteerSlot) -> dict:
    data = {c.key: getattr(slot, c.key) for c in slot.__table__.columns}
    info = await resolve_member_basic(slot.member_id)
    data["member_name"] = info.full_name if info else None
    return data


async def _auto_checkout_if_past(
    db: AsyncSession, slot: VolunteerSlot, opp: VolunteerOpportunity
) -> bool:
    """Auto-checkout a slot if the opportunity end time has passed.

    Called lazily when slots are read (e.g., admin listing, member hours).
    Creates an immutable hours log entry and updates profile aggregates.
    Returns True if a checkout was performed.
    """
    if not slot.checked_in_at or slot.checked_out_at:
        return False
    if not opp.end_time:
        return False

    end_dt = datetime.combine(opp.date, opp.end_time, tzinfo=timezone.utc)
    if datetime.now(timezone.utc) <= end_dt:
        return False

    slot.checked_out_at = end_dt
    slot.status = SlotStatus.COMPLETED
    delta = end_dt - slot.checked_in_at
    slot.hours_logged = round(delta.total_seconds() / 3600, 2)

    hours_log = VolunteerHoursLog(
        member_id=slot.member_id,
        slot_id=slot.id,
        opportunity_id=slot.opportunity_id,
        hours=slot.hours_logged,
        date=opp.date,
        role_id=opp.role_id,
        source="auto_checkout",
    )
    db.add(hours_log)
    await db.commit()

    # Update profile aggregates
    await update_profile_aggregates(db, slot.member_id)
    await db.commit()

    # Best-effort: emit rewards event
    await _emit_volunteer_reward(slot, opp)

    logger.info(
        "Auto-checkout: slot %s for opportunity '%s' — %.2f hours",
        slot.id,
        opp.title,
        slot.hours_logged,
    )
    return True
