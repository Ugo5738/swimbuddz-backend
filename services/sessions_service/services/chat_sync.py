"""Sessions → chat_service integration for pods.

Provisions one chat channel per pod and reconciles its membership against
PodAssignment rows. Best-effort — chat downtime never blocks pod flows.

See chat design doc §10.2 (pod hooks) and
docs/design/POD_MODEL_DESIGN.md for the upstream contract.
"""

from __future__ import annotations

import uuid
from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post

logger = get_logger(__name__)

_CALLING_SERVICE = "sessions"


async def ensure_pod_channel(
    *,
    pod_id: uuid.UUID,
    pod_name: str,
    lead_coach_id: Optional[uuid.UUID] = None,
    has_minors: bool = False,
) -> Optional[uuid.UUID]:
    """Idempotent: ensure a chat channel exists for this pod.

    `lead_coach_id` becomes the channel's initial admin (chat already
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
        "created_by": str(lead_coach_id) if lead_coach_id else None,
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
