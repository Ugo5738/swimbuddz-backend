"""Events → chat_service integration.

Provisions a chat channel per event and reconciles its membership against
RSVPs ("going" → add, anything else → remove). Best-effort — chat
downtime never blocks event flows. See chat design doc §10.3.
"""

from __future__ import annotations

import uuid
from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post

logger = get_logger(__name__)

_CALLING_SERVICE = "events"


async def ensure_event_channel(
    *,
    event_id: uuid.UUID,
    event_title: str,
    created_by_member_id: Optional[uuid.UUID] = None,
) -> Optional[uuid.UUID]:
    """Idempotent: ensure a chat channel exists for this event."""
    settings = get_settings()
    payload = {
        "type": "group",
        "parent_entity_type": "event",
        "parent_entity_id": str(event_id),
        "name": event_title,
        "retention_policy": "event",
        "created_by": str(created_by_member_id) if created_by_member_id else None,
        # Events are open community gatherings — may include minors.
        # Per design §6, channels with minors get extra safeguarding. We
        # default false here; admins flip the flag for kids' events via the
        # chat admin API.
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
                "chat ensure_channel failed for event=%s status=%s body=%s",
                event_id,
                resp.status_code,
                resp.text[:300],
            )
            return None
        return uuid.UUID(resp.json()["channel_id"])
    except Exception as exc:
        logger.warning("chat ensure_channel raised for event=%s: %s", event_id, exc)
        return None


async def reconcile_event_membership(
    *,
    event_id: uuid.UUID,
    member_id: uuid.UUID,
    rsvp_id: uuid.UUID,
    rsvp_status: str,
) -> bool:
    """Sync chat membership from an RSVP transition.

    "going" → add to channel; anything else (maybe / not_going) → remove.
    Idempotent on the chat side. Returns True on success.
    """
    action = "add" if rsvp_status == "going" else "remove"
    settings = get_settings()
    payload = {
        "parent_entity_type": "event",
        "parent_entity_id": str(event_id),
        "member_id": str(member_id),
        "action": action,
        "role": "member",
        "derived_from": "rsvp",
        "derivation_ref": str(rsvp_id),
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
                "chat reconcile %s failed event=%s member=%s status=%s",
                action,
                event_id,
                member_id,
                resp.status_code,
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "chat reconcile %s raised event=%s member=%s: %s",
            action,
            event_id,
            member_id,
            exc,
        )
        return False
