"""Transport → chat_service integration.

Provisions a chat channel per trip and reconciles its membership against
ride bookings.

Mapping:
  * "Trip" channel parent  →  ``session_ride_config_id``
    (one channel per route per session — design §10.4)
  * "Trip member" join     →  RideBooking exists
  * "Trip member" leave    →  RideBooking moved to a different config OR deleted

Caveats / known gaps documented for future maintainers:

  * The design doc imagines a first-class ``transport_trip`` entity.
    ``SessionRideConfig`` is the closest existing analogue (driver-route-
    capacity for one session). When/if a richer Trip model is introduced,
    swap the parent here without touching chat.
  * ``assigned_ride_number`` (split into multiple rides when a config
    overflows capacity) is intentionally NOT in the parent key — riders
    on the same route share one channel even if they're in physically
    different vehicles. Cleaner UX; revisit only if confusion shows up.
  * ``admin_delete_member_transport`` (areas router) bulk-deletes
    bookings via raw SQL and does NOT call us — those members will
    linger as ghost channel members until the nightly reconciliation
    job runs. Acceptable for now; document.

All calls are best-effort: if chat is unavailable, log and continue.
Booking flows must not roll back because chat returned 5xx.
"""

from __future__ import annotations

import uuid
from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post

logger = get_logger(__name__)

_CALLING_SERVICE = "transport"


def _trip_channel_name(area_name: Optional[str]) -> str:
    """Friendly default — refined with session date / pool name later when
    we have cheap cross-service lookups."""
    if area_name:
        return f"Ride · {area_name}"
    return "Ride share"


async def ensure_trip_channel(
    *,
    session_ride_config_id: uuid.UUID,
    area_name: Optional[str] = None,
) -> Optional[uuid.UUID]:
    """Idempotent: ensure a chat channel exists for this trip."""
    settings = get_settings()
    payload = {
        "type": "group",
        "parent_entity_type": "trip",
        "parent_entity_id": str(session_ride_config_id),
        "name": _trip_channel_name(area_name),
        "retention_policy": "trip",
        # Trips are typically adult ride-share but kids occasionally ride too.
        # Default false; flip via the chat admin API for kids' ride-shares.
        "safeguarding_flags": {"has_minors": False},
    }
    try:
        resp = await internal_post(
            service_url=settings.CHAT_SERVICE_URL,
            path="/internal/chat/channels/ensure",
            calling_service=_CALLING_SERVICE,
            json=payload,
        )
        if not resp.is_success:
            logger.warning(
                "chat ensure_channel failed for trip=%s status=%s body=%s",
                session_ride_config_id,
                resp.status_code,
                resp.text[:300],
            )
            return None
        return uuid.UUID(resp.json()["channel_id"])
    except Exception as exc:
        logger.warning(
            "chat ensure_channel raised for trip=%s: %s",
            session_ride_config_id,
            exc,
        )
        return None


async def reconcile_trip_membership(
    *,
    session_ride_config_id: uuid.UUID,
    member_id: uuid.UUID,
    booking_id: uuid.UUID,
    action: str,  # "add" | "remove"
) -> bool:
    """Add or remove a member from a trip channel.

    Idempotent on the chat side. Returns True on success."""
    if action not in {"add", "remove"}:
        raise ValueError(f"action must be 'add' or 'remove', got {action!r}")

    settings = get_settings()
    payload = {
        "parent_entity_type": "trip",
        "parent_entity_id": str(session_ride_config_id),
        "member_id": str(member_id),
        "action": action,
        "role": "member",
        "derived_from": "trip_booking",
        "derivation_ref": str(booking_id),
    }
    try:
        resp = await internal_post(
            service_url=settings.CHAT_SERVICE_URL,
            path="/internal/chat/memberships/reconcile",
            calling_service=_CALLING_SERVICE,
            json=payload,
        )
        if not resp.is_success:
            logger.warning(
                "chat reconcile %s failed trip=%s member=%s status=%s",
                action,
                session_ride_config_id,
                member_id,
                resp.status_code,
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "chat reconcile %s raised trip=%s member=%s: %s",
            action,
            session_ride_config_id,
            member_id,
            exc,
        )
        return False
