"""Phase 3 — Referral models (tables created now, logic deferred)."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.wallet_service.models.enums import ReferralStatus, enum_values
from sqlalchemy import Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class ReferralCode(Base):
    """Unique shareable referral codes per member."""

    __tablename__ = "referral_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_auth_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    code: Mapped[str] = mapped_column(
        String(20), unique=True, index=True, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=50)
    uses_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<ReferralCode {self.code}>"


class ReferralRecord(Base):
    """Tracks referral lifecycle (pending → qualified → rewarded)."""

    __tablename__ = "referral_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    referrer_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    referee_auth_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    referral_code_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[ReferralStatus] = mapped_column(
        SAEnum(
            ReferralStatus,
            name="referral_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=ReferralStatus.PENDING,
        nullable=False,
    )
    referrer_reward_bubbles: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    referee_reward_bubbles: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    referrer_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    referee_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    qualified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rewarded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<ReferralRecord {self.id} {self.status.value}>"
