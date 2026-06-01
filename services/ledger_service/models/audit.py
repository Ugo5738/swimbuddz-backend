"""Finance-team users and the ledger audit log.

`LedgerUser` carries the per-org finance role (design doc §15). `auth_id` is
nullable so an admin can register a teammate by email before they have a
Supabase account; it's bound on first login (impl plan P1.6b). The audit log is
named `ledger_audit_log` to avoid colliding with other services' audit tables.
"""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.ledger_service.models.enums import (
    AuditActionType,
    LedgerRole,
    enum_values,
)
from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class LedgerUser(Base):
    """A finance-team member with a role in one org."""

    __tablename__ = "ledger_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        nullable=False,
        index=True,
    )
    auth_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    role: Mapped[LedgerRole] = mapped_column(
        SAEnum(
            LedgerRole,
            name="ledger_role_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("org_id", "auth_id", name="uq_ledger_users_org_auth"),
    )

    def __repr__(self) -> str:
        who = self.auth_id or self.email
        return f"<LedgerUser {who} role={self.role.value}>"


class AuditLog(Base):
    """Append-only record of sensitive ledger actions."""

    __tablename__ = "ledger_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ledger_users.id"), nullable=True
    )
    actor_service: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    action: Mapped[AuditActionType] = mapped_column(
        SAEnum(
            AuditActionType,
            name="ledger_audit_action_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    subject_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    subject_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (Index("ix_ledger_audit_log_org_created", "org_id", "created_at"),)

    def __repr__(self) -> str:
        return f"<AuditLog {self.action.value} subject={self.subject_type}:{self.subject_id}>"
