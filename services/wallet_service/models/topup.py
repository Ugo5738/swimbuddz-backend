"""WalletTopup model — Bubble purchase / Paystack payment lifecycle."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.wallet_service.models.enums import PaymentMethod, TopupStatus, enum_values
from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class WalletTopup(Base):
    """Tracks Bubble purchase requests and Paystack payment lifecycle."""

    __tablename__ = "wallet_topups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallets.id"), nullable=False, index=True
    )
    member_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    reference: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    bubbles_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    naira_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    exchange_rate: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    payment_reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payment_method: Mapped[PaymentMethod] = mapped_column(
        SAEnum(
            PaymentMethod,
            name="topup_payment_method_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    status: Mapped[TopupStatus] = mapped_column(
        SAEnum(
            TopupStatus,
            name="topup_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=TopupStatus.PENDING,
        nullable=False,
    )
    paystack_authorization_url: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    paystack_access_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    topup_metadata: Mapped[Optional[dict]] = mapped_column(
        "topup_metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationship
    wallet: Mapped["Wallet"] = relationship(back_populates="topups")  # noqa: F821

    __table_args__ = (
        CheckConstraint("bubbles_amount >= 25", name="ck_topup_min_bubbles"),
        CheckConstraint("bubbles_amount <= 5000", name="ck_topup_max_bubbles"),
        CheckConstraint("naira_amount > 0", name="ck_topup_naira_positive"),
    )

    def __repr__(self) -> str:
        return (
            f"<WalletTopup {self.id} {self.bubbles_amount} bubbles {self.status.value}>"
        )
