"""Periodic chat-channel reconciliation for trips (ride-share).

Implements the "derived-membership safety net" described in
CHAT_SERVICE_DESIGN.md §4.2: walks recent ride bookings, groups them by
``session_ride_config_id`` (the trip channel parent — see
``services/chat_sync.py`` for why that's the parent and not ``session_id``),
and re-asserts the corresponding chat channel + member rows.

Mirrors ``services/academy_service/services/chat_reconciliation.py`` —
same shape, different parent entity. Add-only for now; subtract-side
needs a chat-service list endpoint that does not exist yet.

The transport ``chat_sync.py`` docstring already calls out that some bulk
deletion paths (``admin_delete_member_transport``) don't notify chat and
rely on this reconciliation pass to sweep ghost members. Add-only means
this pass won't actually fix that — flagged here for the day a remove-
side ever lands.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from services.transport_service.models import (
    RideArea,
    RideBooking,
    SessionRideConfig,
)
from services.transport_service.services.chat_sync import (
    ensure_trip_channel,
    reconcile_trip_membership,
)

logger = get_logger(__name__)

# Recent bookings only — past trips don't need re-reconciliation, and the
# unbounded scan would grow forever otherwise.
_RECONCILE_WINDOW = timedelta(days=30)


async def reconcile_trip_chat_memberships(db: AsyncSession) -> dict[str, int]:
    """Re-assert chat channels + memberships for trips with recent bookings.

    Idempotent; safe on a schedule and on demand.
    """
    cutoff = utc_now() - _RECONCILE_WINDOW

    bookings = (
        (
            await db.execute(
                select(RideBooking).where(RideBooking.created_at >= cutoff)
            )
        )
        .scalars()
        .all()
    )

    # Group bookings by their parent config (the trip channel key).
    by_config: dict = {}
    for b in bookings:
        by_config.setdefault(b.session_ride_config_id, []).append(b)

    # Resolve area name per config for nice channel labels.
    configs_ensured = 0
    members_attempted = 0
    members_failed = 0

    for config_id, config_bookings in by_config.items():
        # Resolve area name for a friendly channel label. The
        # SessionRideConfig→RideArea relationship isn't declared on the
        # SQLAlchemy model, so join by id explicitly.
        area_name = (
            (
                await db.execute(
                    select(RideArea.name)
                    .join(
                        SessionRideConfig,
                        SessionRideConfig.ride_area_id == RideArea.id,
                    )
                    .where(SessionRideConfig.id == config_id)
                )
            )
            .scalars()
            .first()
        )

        await ensure_trip_channel(
            session_ride_config_id=config_id,
            area_name=area_name,
        )
        configs_ensured += 1

        for booking in config_bookings:
            members_attempted += 1
            ok = await reconcile_trip_membership(
                session_ride_config_id=config_id,
                member_id=booking.member_id,
                booking_id=booking.id,
                action="add",
            )
            if not ok:
                members_failed += 1

    counters = {
        "bookings_scanned": len(bookings),
        "configs_ensured": configs_ensured,
        "members_attempted": members_attempted,
        "members_failed": members_failed,
    }
    logger.info("trip chat reconciliation: %s", counters)
    return counters
