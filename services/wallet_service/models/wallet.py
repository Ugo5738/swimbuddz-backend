"""Wallet model — core Bubble account."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.wallet_service.models.enums import WalletStatus, WalletTier, enum_values
from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class Wallet(Base):
    """Core wallet account. One per member, created on registration."""

    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, index=True, nullable=False
    )
    member_auth_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lifetime_bubbles_purchased: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    lifetime_bubbles_spent: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    lifetime_bubbles_received: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    status: Mapped[WalletStatus] = mapped_column(
        SAEnum(
            WalletStatus,
            name="wallet_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=WalletStatus.ACTIVE,
        nullable=False,
    )
    frozen_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    frozen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    frozen_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    wallet_tier: Mapped[WalletTier] = mapped_column(
        SAEnum(
            WalletTier,
            name="wallet_tier_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=WalletTier.STANDARD,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships (within service)
    transactions: Mapped[list["WalletTransaction"]] = relationship(  # noqa: F821
        back_populates="wallet", lazy="selectin"
    )
    topups: Mapped[list["WalletTopup"]] = relationship(  # noqa: F821
        back_populates="wallet", lazy="selectin"
    )
    grants: Mapped[list["PromotionalBubbleGrant"]] = relationship(  # noqa: F821
        back_populates="wallet", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_wallet_balance_non_negative"),
    )

    def __repr__(self) -> str:
        return f"<Wallet {self.id} member_auth_id={self.member_auth_id} balance={self.balance}>"
