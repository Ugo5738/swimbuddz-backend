"""Members → chat_service integration for pods + location channels.

Provisions chat channels for two parent types:

* **Pod** — one channel per pod, membership derived from PodAssignment.
* **Location** — one channel per city (e.g. "Lagos"), membership derived
  from ``Member.city``. There's no Location entity, so the channel's
  parent_entity_id is a deterministic uuid5 from the slugified city,
  giving us a stable identifier without a DB table.

Best-effort — chat downtime never blocks member/pod flows.

See chat design doc §10.2 (pod hooks), §2 (location channel surface),
and docs/club/POD_OPERATIONS.md for the pod upstream contract.

Ported from `sessions_service/services/chat_sync.py` in May 2026 when
pods moved to members_service. Calling-service header is now "members".
"""

from __future__ import annotations

import uuid
from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post

logger = get_logger(__name__)

_CALLING_SERVICE = "members"

# Stable namespace for deriving location channel ids from city slugs. Any
# UUID will do — once chosen it must never change, because the channel
# parent_entity_id depends on it.
_LOCATION_NAMESPACE = uuid.UUID("c1a7e0c0-5b1e-4e3a-8c8d-7e3c1a7e0c00")


def slugify_city(city: str) -> str:
    """Lowercase + strip + collapse internal whitespace → hyphen."""
    return "-".join(city.strip().lower().split())


def location_id_for_city(city: str) -> uuid.UUID:
    """Deterministic UUID derived from a city name. Stable across services."""
    return uuid.uuid5(_LOCATION_NAMESPACE, slugify_city(city))


async def ensure_pod_channel(
    *,
    pod_id: uuid.UUID,
    pod_name: str,
    pod_lead_id: Optional[uuid.UUID] = None,
    has_minors: bool = False,
) -> Optional[uuid.UUID]:
    """Idempotent: ensure a chat channel exists for this pod.

    `pod_lead_id` becomes the channel's initial admin (chat already
    promotes `created_by` to admin role on first creation). `has_minors`
    flips the safeguarding flag — admins/safeguarding admins can refine
    later via the chat admin API."""
    settings = get_settings()
    payload = {
        "type": "group",
        "parent_entity_type": "pod",
        "parent_entity_id": str(pod_id),
        "name": pod_name,
        "retention_policy": "pod",
        "created_by": str(pod_lead_id) if pod_lead_id else None,
        "safeguarding_flags": {"has_minors": has_minors},
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
                "chat ensure_channel failed for pod=%s status=%s body=%s",
                pod_id,
                resp.status_code,
                resp.text[:300],
            )
            return None
        return uuid.UUID(resp.json()["channel_id"])
    except Exception as exc:
        logger.warning("chat ensure_channel raised for pod=%s: %s", pod_id, exc)
        return None


async def reconcile_pod_membership(
    *,
    pod_id: uuid.UUID,
    member_id: uuid.UUID,
    assignment_id: uuid.UUID,
    action: str,  # "add" | "remove"
) -> bool:
    """Add or remove a member from a pod's chat channel.

    Idempotent on the chat side (re-add is a no-op for active members,
    re-remove for already-left ones). Returns True on success."""
    if action not in {"add", "remove"}:
        raise ValueError(f"action must be 'add' or 'remove', got {action!r}")

    settings = get_settings()
    payload = {
        "parent_entity_type": "pod",
        "parent_entity_id": str(pod_id),
        "member_id": str(member_id),
        "action": action,
        "role": "member",
        "derived_from": "pod_assignment",
        "derivation_ref": str(assignment_id),
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
                "chat reconcile %s failed pod=%s member=%s status=%s",
                action,
                pod_id,
                member_id,
                resp.status_code,
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "chat reconcile %s raised pod=%s member=%s: %s",
            action,
            pod_id,
            member_id,
            exc,
        )
        return False


# ─── Location (Club General) channels ────────────────────────────────────


async def ensure_location_channel(
    *,
    city: str,
) -> Optional[uuid.UUID]:
    """Idempotent: ensure a chat channel exists for this city.

    Parent_entity_id is the deterministic uuid derived from the slugified
    city name (see ``location_id_for_city``). Same city → same channel,
    forever, across services.
    """
    if not city or not city.strip():
        return None

    location_id = location_id_for_city(city)
    settings = get_settings()
    payload = {
        "type": "group",
        "parent_entity_type": "location",
        "parent_entity_id": str(location_id),
        "name": city.strip(),
        "retention_policy": "location",
        # Location channels span all ages — kids and adults in the same
        # city. Safeguarding admins can refine via the chat admin API.
        "safeguarding_flags": {"has_minors": True},
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
                "chat ensure_channel failed for location=%s status=%s body=%s",
                city,
                resp.status_code,
                resp.text[:300],
            )
            return None
        return uuid.UUID(resp.json()["channel_id"])
    except Exception as exc:
        logger.warning("chat ensure_channel raised for location=%s: %s", city, exc)
        return None


async def reconcile_location_membership(
    *,
    city: str,
    member_id: uuid.UUID,
    action: str,  # "add" | "remove"
) -> bool:
    """Add or remove a member from a city's location channel.

    Idempotent on the chat side. ``derivation_ref`` is the member's own
    id — the "derived from" record is "this member lives in this city".
    """
    if action not in {"add", "remove"}:
        raise ValueError(f"action must be 'add' or 'remove', got {action!r}")
    if not city or not city.strip():
        return False

    location_id = location_id_for_city(city)
    settings = get_settings()
    payload = {
        "parent_entity_type": "location",
        "parent_entity_id": str(location_id),
        "member_id": str(member_id),
        "action": action,
        "role": "member",
        # No "city_assignment" derivation enum exists; reuse "role" —
        # the closest match for "derived from a member-level attribute".
        "derived_from": "role",
        "derivation_ref": str(member_id),
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
                "chat reconcile %s failed location=%s member=%s status=%s",
                action,
                city,
                member_id,
                resp.status_code,
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "chat reconcile %s raised location=%s member=%s: %s",
            action,
            city,
            member_id,
            exc,
        )
        return False
