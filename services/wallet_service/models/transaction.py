"""WalletTransaction model — immutable ledger."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.wallet_service.models.enums import (
    TransactionDirection,
    TransactionStatus,
    TransactionType,
    enum_values,
)
from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class WalletTransaction(Base):
    """Immutable ledger of all balance changes. Source of truth."""

    __tablename__ = "wallet_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallets.id"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    transaction_type: Mapped[TransactionType] = mapped_column(
        SAEnum(
            TransactionType,
            name="transaction_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    direction: Mapped[TransactionDirection] = mapped_column(
        SAEnum(
            TransactionDirection,
            name="transaction_direction_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_before: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(
            TransactionStatus,
            name="transaction_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=TransactionStatus.PENDING,
        nullable=False,
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    service_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reference_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reference_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    initiated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    txn_metadata: Mapped[Optional[dict]] = mapped_column(
        "txn_metadata", JSONB, nullable=True
    )
    reversed_by_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reversal_of_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationship
    wallet: Mapped["Wallet"] = relationship(back_populates="transactions")  # noqa: F821

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),
        Index(
            "ix_wallet_transactions_wallet_created",
            "wallet_id",
            "created_at",
            postgresql_using="btree",
        ),
    )

    def __repr__(self) -> str:
        return f"<WalletTransaction {self.id} {self.direction.value} {self.amount}>"
