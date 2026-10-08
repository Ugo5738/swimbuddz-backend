"""Pool Access is not a Club session or a pool rental.

Published access windows, customer bookings, individual redemptions and
partner reconciliation remain separate from the training-session domain.
All amounts are in minor currency units (kobo for NGN).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from libs.db.base import Base
from libs.common.datetime_utils import utc_now


class PoolAccessOffer(Base):
    __tablename__ = "pool_access_offers"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="ck_pool_access_offer_capacity"),
        CheckConstraint("selling_price_kobo > 0", name="ck_pool_access_offer_price"),
        CheckConstraint("negotiated_cost_kobo >= 0", name="ck_pool_access_offer_cost"),
        CheckConstraint("ends_at > starts_at", name="ck_pool_access_offer_window"),
        CheckConstraint(
            "cost_basis IN ('per_person', 'per_group')",
            name="ck_pool_access_cost_basis",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'paused')",
            name="ck_pool_access_offer_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pools.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    selling_price_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    negotiated_cost_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="NGN")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    public_booking_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    self_directed_permitted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    admissions_require_lifeguard: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    amenities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    access_rules: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cancellation_policy: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PoolAccessBooking(Base):
    __tablename__ = "pool_access_bookings"
    __table_args__ = (
        CheckConstraint("headcount > 0", name="ck_pool_access_booking_headcount"),
        CheckConstraint(
            "status IN ('pending_payment', 'confirmed', 'cancelled')",
            name="ck_pool_access_booking_status",
        ),
        UniqueConstraint(
            "buyer_auth_id", "idempotency_key", name="uq_pool_access_buyer_idempotency"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    offer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pool_access_offers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    buyer_auth_id: Mapped[str | None] = mapped_column(
        String(255), index=True, nullable=True
    )
    buyer_email: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    headcount: Mapped[int] = mapped_column(Integer, nullable=False)
    selling_total_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    negotiated_cost_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending_payment"
    )
    checkout_reference: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(
        String(160), unique=True, nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hold_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PoolAccessAdmission(Base):
    __tablename__ = "pool_access_admissions"
    __table_args__ = (
        UniqueConstraint(
            "booking_id", "ordinal", name="uq_pool_access_admission_ordinal"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pool_access_bookings.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    guest_name: Mapped[str] = mapped_column(String(150), nullable=False)
    checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checked_in_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PoolAccessReconciliation(Base):
    """Immutable statement snapshot, NOT a payout or an accounting posting."""

    __tablename__ = "pool_access_reconciliations"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pool_access_bookings.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    verified_admissions: Mapped[int] = mapped_column(Integer, nullable=False)
    payable_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    reconciled_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PoolAccessPartnerOperator(Base):
    """Pool-scoped reception access. Never a general platform administrator."""

    __tablename__ = "pool_access_partner_operators"
    __table_args__ = (
        UniqueConstraint("pool_id", "auth_id", name="uq_pool_access_operator_scope"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pools.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    auth_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
