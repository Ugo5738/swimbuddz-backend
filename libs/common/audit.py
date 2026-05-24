"""Canonical audit-log shape shared across services (B4).

Three services (wallet, store, chat) each keep their own per-service
audit table — sharing one physical table would violate the service-
isolation rule (no shared cross-service tables; see
`docs/reference/SERVICE_COMMUNICATION.md` and project memory
`project_no_cross_service_fks`). What we DO share is the column
**shape** — so future cross-domain audit queries see consistent fields,
and new audit code looks the same in every service.

Each service's audit model inherits :class:`AuditLogMixin` to adopt the
canonical columns. Per-service concerns stay on the concrete model
(``__tablename__``, indexes, service-specific enums validated at the
write site).

See ``docs/design/B4_AUDIT_LOG_UNIFICATION.md`` for the full design.

Usage in a service model::

    from libs.common.audit import AuditLogMixin
    from libs.db.base import Base

    class WalletAuditLog(AuditLogMixin, Base):
        __tablename__ = "wallet_audit_logs"

        __table_args__ = (
            Index("ix_wallet_audit_entity", "entity_id", "created_at"),
        )

Usage at a write site::

    from libs.common.audit import DOMAIN_WALLET, make_action, parse_uuid_or_none

    audit = WalletAuditLog(
        domain=DOMAIN_WALLET,
        entity_type="wallet",
        entity_id=wallet_id,
        action=make_action(DOMAIN_WALLET, "freeze"),  # "wallet.freeze"
        actor_id=parse_uuid_or_none(admin.user_id),
        actor_label=admin.user_id,
        old_value={"status": old_status},
        new_value={"status": "frozen", "reason": reason},
        reason=reason,
        ip_address=request.client.host if request.client else None,
    )
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Final, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now

# ── Domain constants ─────────────────────────────────────────────────
# Use these instead of bare strings at write sites so all writers in a
# service agree on the same domain label.
DOMAIN_WALLET: Final[str] = "wallet"
DOMAIN_STORE: Final[str] = "store"
DOMAIN_CHAT: Final[str] = "chat"


def make_action(domain: str, verb: str) -> str:
    """Namespace a service-local action verb under its domain.

    Example: ``make_action(DOMAIN_WALLET, "freeze")`` → ``"wallet.freeze"``.
    Use the per-service enum at the write site to validate ``verb`` so
    each service keeps its own action vocabulary, but the persisted
    value is consistently namespaced across services.
    """
    return f"{domain}.{verb}"


def parse_uuid_or_none(value: object) -> Optional[uuid.UUID]:
    """Best-effort UUID parse.

    Returns the parsed UUID if ``value`` is already a UUID or looks
    like one; ``None`` otherwise. Used by writers to set
    :attr:`AuditLogMixin.actor_id` from string-typed admin/user IDs
    that may or may not be UUIDs (e.g. wallet's pre-Supabase
    ``performed_by`` strings).

    Always also set ``actor_label`` from the original string so the
    human-readable actor is preserved when the parse fails.
    """
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


# ── SQLAlchemy mixin ────────────────────────────────────────────────
class AuditLogMixin:
    """Canonical column shape for per-service audit tables.

    Inherit alongside ``Base`` and declare ``__tablename__`` /
    ``__table_args__`` on the subclass. Indexes are intentionally NOT
    declared here — they're per-service (a wallet audit query filters
    by entity_id+created_at; a chat audit query may need different
    composites).

    Columns:
      * ``id`` — PK, UUID.
      * ``domain`` — service that wrote the row ("wallet"/"store"/"chat").
      * ``entity_type`` — service-local entity classification.
      * ``entity_id`` — UUID of the row the action touched.
      * ``action`` — service-namespaced verb (e.g. "wallet.freeze").
      * ``actor_id`` — UUID actor if known; ``NULL`` = system/service.
      * ``actor_label`` — string actor when no UUID is available
        (preserves human-readable IDs like "seed-admin").
      * ``old_value`` / ``new_value`` — JSONB snapshots.
      * ``reason`` — optional-common; some services made it required
        historically (wallet) but the canonical shape demotes it to
        nullable so chat-style events can omit it.
      * ``ip_address`` — optional, kept narrow at 45 chars (IPv6).
      * ``created_at`` — write time, timezone-aware UTC.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    actor_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    old_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


# ── Pydantic schema ─────────────────────────────────────────────────
class AuditLogRead(BaseModel):
    """Canonical Pydantic view of an audit log row.

    Service routers can either return this directly (for cross-domain
    consumers) or subclass it to add service-specific helper fields.
    """

    id: uuid.UUID
    domain: str
    entity_type: str
    entity_id: uuid.UUID
    action: str
    actor_id: Optional[uuid.UUID] = None
    actor_label: Optional[str] = None
    old_value: Optional[dict] = None
    new_value: Optional[dict] = None
    reason: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
