"""Academy → chat_service integration.

Provisions a chat channel per cohort and reconciles its membership against
academy enrollments. All calls are best-effort: if chat is unavailable, we
log and continue — the enrollment flow must not roll back because chat
returned 5xx.

See chat design doc §10.1.
"""

from __future__ import annotations

import uuid
from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post

logger = get_logger(__name__)

_CALLING_SERVICE = "academy"

# Channel-name template — kept here so chat doesn't need to know about Cohort
# naming conventions. Cohort.name is something like "Adult Beginners — June".
_COHORT_CHANNEL_NAME_FMT = "{cohort_name}"


async def ensure_cohort_channel(
    *,
    cohort_id: uuid.UUID,
    cohort_name: str,
    created_by_member_id: Optional[uuid.UUID] = None,
    has_minors: bool = False,
) -> Optional[uuid.UUID]:
    """Idempotent: ensure a chat channel exists for this cohort.

    Returns the channel id on success, or None if chat is unreachable. Safe
    to call repeatedly — chat returns the existing row on subsequent calls.

    `has_minors` flips the safeguarding flag set on creation. Default false;
    callers that know the cohort age range should set it accordingly.
    """
    settings = get_settings()
    payload = {
        "type": "group",
        "parent_entity_type": "cohort",
        "parent_entity_id": str(cohort_id),
        "name": _COHORT_CHANNEL_NAME_FMT.format(cohort_name=cohort_name),
        "retention_policy": "cohort",
        "created_by": str(created_by_member_id) if created_by_member_id else None,
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
                "chat_service ensure_channel failed for cohort=%s status=%s body=%s",
                cohort_id,
                resp.status_code,
                resp.text[:300],
            )
            return None
        body = resp.json()
        return uuid.UUID(body["channel_id"])
    except Exception as exc:
        # We never let chat errors break academy flows. Log and move on.
        logger.warning(
            "chat_service ensure_channel raised for cohort=%s: %s", cohort_id, exc
        )
        return None


async def reconcile_cohort_membership(
    *,
    cohort_id: uuid.UUID,
    member_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    action: str,  # "add" | "remove"
) -> bool:
    """Add or remove a member from the cohort's chat channel.

    Returns True on success, False on failure (best-effort — caller should
    not retry inline; the nightly reconciliation job catches drift)."""
    if action not in {"add", "remove"}:
        raise ValueError(f"action must be 'add' or 'remove', got {action!r}")

    settings = get_settings()
    payload = {
        "parent_entity_type": "cohort",
        "parent_entity_id": str(cohort_id),
        "member_id": str(member_id),
        "action": action,
        "role": "member",
        "derived_from": "enrollment",
        "derivation_ref": str(enrollment_id),
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
                "chat reconcile %s failed cohort=%s member=%s status=%s",
                action,
                cohort_id,
                member_id,
                resp.status_code,
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "chat reconcile %s raised cohort=%s member=%s: %s",
            action,
            cohort_id,
            member_id,
            exc,
        )
        return False
