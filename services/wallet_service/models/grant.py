"""PromotionalBubbleGrant and WalletAuditLog models."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.common.audit import AuditLogMixin
from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.wallet_service.models.enums import GrantType, enum_values


class PromotionalBubbleGrant(Base):
    """Tracks promotional/bonus Bubbles issued by admins or system rules."""

    __tablename__ = "promotional_bubble_grants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallets.id"), nullable=False, index=True
    )
    member_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    grant_type: Mapped[GrantType] = mapped_column(
        SAEnum(
            GrantType,
            name="grant_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    bubbles_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    campaign_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    bubbles_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    granted_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    grant_metadata: Mapped[Optional[dict]] = mapped_column(
        "grant_metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # Relationship
    wallet: Mapped["Wallet"] = relationship(back_populates="grants")  # noqa: F821

    __table_args__ = (
        CheckConstraint("bubbles_amount > 0", name="ck_grant_amount_positive"),
        CheckConstraint(
            "bubbles_remaining >= 0", name="ck_grant_remaining_non_negative"
        ),
        CheckConstraint(
            "bubbles_remaining <= bubbles_amount",
            name="ck_grant_remaining_lte_amount",
        ),
    )

    def __repr__(self) -> str:
        return f"<PromotionalBubbleGrant {self.id} {self.grant_type.value} {self.bubbles_amount}>"


class WalletAuditLog(AuditLogMixin, Base):
    """Tracks sensitive admin operations on wallets.

    Adopts the canonical audit-log shape from :mod:`libs.common.audit`
    (B4 — see ``docs/design/B4_AUDIT_LOG_UNIFICATION.md``). Per-service
    table; only the **shape** is shared with store + chat audit logs.

    Service-specific conventions for writers in this table:
      * ``domain`` is always ``"wallet"`` (see ``DOMAIN_WALLET``).
      * ``entity_type`` is ``"wallet"`` for wallet-scoped actions.
      * ``entity_id`` is the wallet's UUID (carries the data that
        used to live in the pre-B4 ``wallet_id`` column).
      * ``action`` is the per-service :class:`AuditAction` enum value
        namespaced via ``make_action(DOMAIN_WALLET, …)`` —
        e.g. ``"wallet.freeze"``. Validate against the enum at the
        write site; the column itself stores plain strings so other
        services don't import this enum.
      * ``actor_id`` is the admin's UUID when parseable from
        ``admin.user_id``; ``actor_label`` always carries the raw
        string for human readability (preserves seed/historic
        non-UUID values like ``"seed-admin"``).
    """

    __tablename__ = "wallet_audit_logs"

    __table_args__ = (
        # Lookups in the admin UI filter by wallet (entity_id), newest first.
        # Replaces the old standalone wallet_id index.
        Index("ix_wallet_audit_entity_created", "entity_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<WalletAuditLog {self.id} {self.action}>"
