"""Standalone, self-paying guest reservation for a Session."""

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class GuestPass(Base):
    __tablename__ = "guest_passes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    guardian_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    guardian_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    waiver_accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    marketing_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    referral_code: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, index=True
    )
    referrer_auth_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    price_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    additional_charges: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    total_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_reference: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="pending_payment",
        server_default="pending_payment",
    )
    referral_reward_kobo: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100_000, server_default="100000"
    )
    referral_reward_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="not_eligible",
        server_default="not_eligible",
    )
    referral_reward_reference: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    attended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_swim_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assessment_result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    converted_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("session_id", "phone", name="uq_guest_pass_session_phone"),
        CheckConstraint("price_kobo >= 0", name="ck_guest_pass_price_nonnegative"),
        CheckConstraint("total_kobo >= price_kobo", name="ck_guest_pass_total_valid"),
        CheckConstraint(
            "actual_swim_minutes IS NULL OR actual_swim_minutes >= 0",
            name="ck_guest_pass_minutes_nonnegative",
        ),
    )
