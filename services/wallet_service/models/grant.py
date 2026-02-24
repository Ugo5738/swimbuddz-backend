"""PromotionalBubbleGrant and WalletAuditLog models."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.wallet_service.models.enums import AuditAction, GrantType, enum_values
from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


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


class WalletAuditLog(Base):
    """Tracks sensitive admin operations on wallets."""

    __tablename__ = "wallet_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    action: Mapped[AuditAction] = mapped_column(
        SAEnum(
            AuditAction,
            name="audit_action_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    performed_by: Mapped[str] = mapped_column(String, nullable=False)
    old_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<WalletAuditLog {self.id} {self.action.value}>"
