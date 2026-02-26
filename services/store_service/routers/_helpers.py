"""Shared admin helper functions for store routers."""

import uuid
from typing import Optional

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
):
    """Log an audit event."""
    audit_log = StoreAuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        old_value=old_value,
        new_value=new_value,
        performed_by=performed_by,
        notes=notes,
    )
    db.add(audit_log)
