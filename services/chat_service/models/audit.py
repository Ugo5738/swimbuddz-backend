"""Chat audit log.

Every significant chat event (message sent/edited/deleted, membership changes,
moderation actions, role changes) is recorded here. Retention is indefinite by
default — see design doc §4.1, §9.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.chat_service.models.enums import ChatAuditAction, enum_values


class ChatAuditLog(Base):
    """Append-only audit trail of chat events.

    Rows are never updated or deleted through normal code paths. Only hard-delete
    by retention tooling (not implemented in Phase 0).
    """

    __tablename__ = "chat_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Actor is the member who performed the action; null for system actions.
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    action: Mapped[ChatAuditAction] = mapped_column(
        SAEnum(
            ChatAuditAction,
            name="chat_audit_action_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    # Optional scope refs. Not enforced as FKs because audit rows must survive
    # hard-deletion of their subjects (e.g. a channel purged per retention).
    channel_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Subject: the member the action was performed against (e.g. removed member,
    # reported sender, role recipient).
    subject_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Action-specific payload — e.g. {"old_role": "member", "new_role": "moderator"}.
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index("ix_chat_audit_log_channel", "channel_id", "created_at"),
        Index("ix_chat_audit_log_actor", "actor_id", "created_at"),
        Index("ix_chat_audit_log_subject", "subject_member_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ChatAuditLog {self.id} action={self.action.value} "
            f"actor={self.actor_id} subject={self.subject_member_id}>"
        )
