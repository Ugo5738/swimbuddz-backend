"""Store supplier models: supplier management and payouts."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.store_service.models.enums import (
    PayoutStatus,
    SupplierStatus,
    enum_values,
)


class Supplier(Base):
    """Supplier entity -- any entity that sources products for the store.

    SwimBuddz is Supplier #001. Every rule, constraint, and SLA that will
    apply to external suppliers is first tested internally.
    """

    __tablename__ = "store_suppliers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    # Contact
    contact_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Financial
    commission_percent: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )  # e.g., 15.00 = 15% platform take
    payout_bank_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payout_account_number: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    payout_account_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    # Status
    is_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    status: Mapped[SupplierStatus] = mapped_column(
        SAEnum(
            SupplierStatus,
            values_callable=enum_values,
            name="store_supplier_status_enum",
        ),
        default=SupplierStatus.ACTIVE,
        server_default="active",
    )
    probation_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Metrics (updated periodically)
    total_products: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_orders: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    average_fulfillment_hours: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 2), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    products = relationship("Product", back_populates="supplier")
    payouts = relationship(
        "SupplierPayout", back_populates="supplier", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Supplier {self.name}>"


class SupplierPayout(Base):
    """Supplier payout records for settlement tracking.

    Each payout covers a date range and includes commission calculation.
    Status lifecycle: pending -> processing -> paid (or failed).
    """

    __tablename__ = "store_supplier_payouts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("store_suppliers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    payout_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    payout_period_end: Mapped[date] = mapped_column(Date, nullable=False)

    total_sales_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    commission_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payout_amount_ngn: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    status: Mapped[PayoutStatus] = mapped_column(
        SAEnum(
            PayoutStatus,
            values_callable=enum_values,
            name="store_payout_status_enum",
        ),
        default=PayoutStatus.PENDING,
        server_default="pending",
    )

    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payment_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    supplier = relationship("Supplier", back_populates="payouts")

    def __repr__(self):
        return f"<SupplierPayout {self.supplier_id} {self.payout_period_start}-{self.payout_period_end}>"
