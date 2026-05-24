"""Shared admin helper functions for store routers."""

import uuid
from typing import Optional

from libs.common.audit import DOMAIN_STORE, make_action, parse_uuid_or_none
from services.store_service.models import AuditEntityType, StoreAuditLog
from sqlalchemy.ext.asyncio import AsyncSession


async def log_audit(
    db: AsyncSession,
    entity_type: AuditEntityType,
    entity_id: uuid.UUID,
    action: str,
    performed_by: str,
    old_value: Optional[dict] = None,
    new_value: Optional[dict] = None,
    notes: Optional[str] = None,
    ip_address: Optional[str] = None,
):
    """Log an audit event using the canonical (B4) shape.

    ``action`` is the service-local verb (e.g. ``"price_changed"``); it
    is namespaced to ``"store.price_changed"`` on write. ``performed_by``
    is parsed best-effort into ``actor_id`` (UUID) and always preserved
    on ``actor_label``. ``notes`` maps to the canonical ``reason`` field.
    """
    audit_log = StoreAuditLog(
        domain=DOMAIN_STORE,
        entity_type=entity_type.value,
        entity_id=entity_id,
        action=make_action(DOMAIN_STORE, action),
        actor_id=parse_uuid_or_none(performed_by),
        actor_label=performed_by,
        old_value=old_value,
        new_value=new_value,
        reason=notes,
        ip_address=ip_address,
    )
    db.add(audit_log)
