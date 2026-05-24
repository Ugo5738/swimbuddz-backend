"""Chat audit log.

Every significant chat event (message sent/edited/deleted, membership changes,
moderation actions, role changes) is recorded here. Retention is indefinite by
default — see design doc §4.1, §9.

The row shape inherits the canonical B4 audit fields from
:class:`libs.common.audit.AuditLogMixin` so admin/audit consumers see the
same columns in every service. ``channel_id`` and ``subject_member_id``
remain as chat-specific *denormalized* scope columns — they make admin
filters like "all audit events in channel X" or "everything done to
member Y" fast without needing a JSONB or join lookup. The legacy
``message_id`` is dropped because for message-typed actions the
canonical ``entity_id`` already holds it (with ``entity_type='message'``).
"""

import uuid
from typing import Optional

from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.audit import AuditLogMixin
from libs.db.base import Base


class ChatAuditLog(AuditLogMixin, Base):
    """Append-only audit trail of chat events.

    Rows are never updated or deleted through normal code paths. Only hard-delete
    by retention tooling (not implemented in Phase 0).

    Canonical columns (from :class:`AuditLogMixin`):
      ``id``, ``domain``, ``entity_type``, ``entity_id``, ``action``,
      ``actor_id``, ``actor_label``, ``old_value``, ``new_value``,
      ``reason``, ``ip_address``, ``created_at``.

    Chat-specific extras:
      * ``channel_id`` — the channel the event happened in. Denormalized so
        admin "show all events in channel X" is a simple indexed lookup.
        Nullable for system-wide events.
      * ``subject_member_id`` — the member targeted by the action (e.g.
        removed member, reported sender, role recipient). Denormalized so
        "everything done to member Y" is a simple indexed lookup.
        Nullable when the action has no specific subject member.
    """

    __tablename__ = "chat_audit_log"

    # ── Chat-specific denormalized scope columns ──────────────────────
    # Not on the canonical mixin — they're chat-specific admin filters.
    # Not FKs because audit rows must survive hard-deletion of their
    # subjects (e.g. a channel purged per retention).
    channel_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    subject_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    __table_args__ = (
        Index("ix_chat_audit_log_channel", "channel_id", "created_at"),
        Index("ix_chat_audit_log_actor", "actor_id", "created_at"),
        Index("ix_chat_audit_log_subject", "subject_member_id", "created_at"),
        Index("ix_chat_audit_log_entity_created", "entity_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ChatAuditLog {self.id} action={self.action} "
            f"actor={self.actor_id} subject={self.subject_member_id}>"
        )
