"""Periodic chat-channel reconciliation for events.

Implements the "derived-membership safety net" described in
CHAT_SERVICE_DESIGN.md §4.2: walks recent + upcoming events with `going`
RSVPs and re-asserts the corresponding chat channel + member rows.

Mirrors ``services/academy_service/services/chat_reconciliation.py`` —
same shape, different parent entity. Add-only for now; subtract-side
needs a chat-service list endpoint that does not exist yet.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from services.events_service.models import Event, EventRSVP
from services.events_service.services.chat_sync import (
    ensure_event_channel,
    reconcile_event_membership,
)

logger = get_logger(__name__)

# Window for "active" events. Past events keep their channel for history
# but new reconciliation runs only touch recent + upcoming ones — avoids
# unbounded scans as the event archive grows.
_RECONCILE_WINDOW = timedelta(days=30)


async def reconcile_event_chat_memberships(db: AsyncSession) -> dict[str, int]:
    """Re-assert chat channels + memberships for events with recent activity.

    Idempotent; safe on a schedule and on demand.
    """
    cutoff = utc_now() - _RECONCILE_WINDOW
    events = (
        (await db.execute(select(Event).where(Event.start_time >= cutoff)))
        .scalars()
        .all()
    )

    events_ensured = 0
    members_attempted = 0
    members_failed = 0

    for event in events:
        going = (
            (
                await db.execute(
                    select(EventRSVP).where(
                        EventRSVP.event_id == event.id,
                        EventRSVP.status == "going",
                    )
                )
            )
            .scalars()
            .all()
        )
        if not going:
            continue

        await ensure_event_channel(
            event_id=event.id,
            event_title=event.title,
            created_by_member_id=event.created_by,
        )
        events_ensured += 1

        for rsvp in going:
            members_attempted += 1
            ok = await reconcile_event_membership(
                event_id=event.id,
                member_id=rsvp.member_id,
                rsvp_id=rsvp.id,
                rsvp_status="going",
            )
            if not ok:
                members_failed += 1

    counters = {
        "events_scanned": len(events),
        "events_ensured": events_ensured,
        "members_attempted": members_attempted,
        "members_failed": members_failed,
    }
    logger.info("event chat reconciliation: %s", counters)
    return counters
