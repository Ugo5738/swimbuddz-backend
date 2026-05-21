"""Audit-log writer for admin-side media access.

Every admin path that surfaces or extracts a private-bucket asset
calls one of these helpers to record the access on
``media_audit_logs``. The helpers are intentionally tiny — the
write is best-effort and never blocks the caller's response. If
audit insertion fails the user-facing operation still succeeds and
the failure is logged loudly.

See ``docs/design/ACADEMY_ADMIN_CONTROLS_DESIGN.md`` §4.3 for the
action taxonomy and B4 shape rationale.
"""

from __future__ import annotations

import uuid
from typing import Iterable, Optional

from fastapi import Request
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.principals import is_synthetic_principal
from sqlalchemy.ext.asyncio import AsyncSession

from services.media_service.models import MediaAuditLog

logger = get_logger(__name__)


# Action namespace constants. Reusing the same strings in client code
# prevents typos and makes "all download events" a one-line predicate.
ACTION_LIST = "media.admin.list"
ACTION_VIEW = "media.admin.view"
ACTION_DOWNLOAD = "media.admin.download"


def _actor_label(actor: AuthUser) -> str:
    """Best-effort human-readable label for an audit row.

    Stored denormalised so the audit row stays readable after the
    underlying user is renamed, deleted, or pseudonymised.
    """
    if is_synthetic_principal(
        uuid.UUID(actor.user_id) if isinstance(actor.user_id, str) else actor.user_id
    ):
        # Shouldn't normally happen on the request path (synthetic
        # principals don't carry real JWTs), but keep the fallback
        # consistent.
        return "system"
    return actor.email or str(actor.user_id)


def _client_ip(request: Optional[Request]) -> Optional[str]:
    """Extract the client IP from the request, honouring the
    upstream proxy chain (X-Forwarded-For) when present."""
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # Left-most IP is the original client per RFC 7239.
        return fwd.split(",", 1)[0].strip() or None
    if request.client:
        return request.client.host
    return None


async def write_audit(
    db: AsyncSession,
    *,
    action: str,
    actor: AuthUser,
    entity_id: uuid.UUID,
    request: Optional[Request] = None,
    reason: Optional[str] = None,
    old_value: Optional[dict] = None,
    new_value: Optional[dict] = None,
    flush: bool = False,
) -> None:
    """Write a single ``media_audit_logs`` row.

    The caller's transaction owns the commit — pass ``flush=True``
    when you want the row to land in this transaction's snapshot
    (e.g. before issuing a presigned URL the row should already be
    visible). Otherwise we rely on the caller's commit.
    """
    try:
        row = MediaAuditLog(
            domain="media",
            entity_type="media_item",
            entity_id=entity_id,
            action=action,
            actor_id=uuid.UUID(actor.user_id)
            if isinstance(actor.user_id, str)
            else actor.user_id,
            actor_label=_actor_label(actor),
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            ip_address=_client_ip(request),
        )
        db.add(row)
        if flush:
            await db.flush()
    except Exception as e:
        # Audit failures must NOT take down the user's operation.
        # Log with high severity so it surfaces in monitoring.
        logger.error(
            "Failed to write media_audit_logs row (action=%s entity=%s): %s",
            action,
            entity_id,
            e,
        )


async def write_audit_bulk(
    db: AsyncSession,
    *,
    action: str,
    actor: AuthUser,
    entity_ids: Iterable[uuid.UUID],
    request: Optional[Request] = None,
    reason: Optional[str] = None,
) -> None:
    """Write one audit row per entity_id.

    Useful for list endpoints — every item surfaced gets its own
    row so a future query ``WHERE entity_id = <media>`` returns all
    administrative access of that asset regardless of which endpoint
    surfaced it.
    """
    for entity_id in entity_ids:
        await write_audit(
            db,
            action=action,
            actor=actor,
            entity_id=entity_id,
            request=request,
            reason=reason,
        )


__all__ = [
    "ACTION_DOWNLOAD",
    "ACTION_LIST",
    "ACTION_VIEW",
    "write_audit",
    "write_audit_bulk",
]
