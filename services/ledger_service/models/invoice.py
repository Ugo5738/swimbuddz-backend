"""Invoices — issuable financial documents tied to the org's books (design §13).

R5-PR1 is the dependency-free foundation: the invoice header + lines + a gapless,
concurrency-safe per-(org, year) number (``SB-2026-000123``). VAT/WHT amounts,
FIRS IRN/submission, and PDF rendering are deliberately deferred — the model
carries nullable ``tax_*`` / ``irn`` / ``firs_*`` fields and ``tax_minor`` defaults
to 0, so those layer on without a reshape once the tax determinations and FIRS
credentials are in hand.

Numbering: ``InvoiceSequence`` holds one counter per (org, prefix, year).
Allocation is an atomic ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` on that
row (see services/invoices.py), so concurrent issues serialise on the row and the
sequence is gapless across committed transactions (a rolled-back issue releases
its number). All three tables are org-keyed; RLS policies in a companion migration.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class InvoiceSequence(Base):
    """Per-(org, prefix, year) monotonic counter for gapless invoice numbers."""

    __tablename__ = "invoice_sequences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        nullable=False,
        index=True,
    )
    prefix: Mapped[str] = mapped_column(String, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    last_number: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("org_id", "prefix", "year", name="uq_invoice_sequence"),
    )

    def __repr__(self) -> str:
        return f"<InvoiceSequence {self.prefix}-{self.year} last={self.last_number}>"


class Invoice(Base):
    """An issued (or draft) invoice header."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        nullable=False,
        index=True,
    )
    invoice_number: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # draft | issued | void
    status: Mapped[str] = mapped_column(
        String, default="issued", nullable=False, index=True
    )
    # What the invoice is for — links back to the operational row that prompted it.
    source_service: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    # Customer (member auth id, corporate id, …) + snapshot of their details.
    customer_ref: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    customer_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    customer_email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    customer_tin: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, default="NGN", nullable=False)
    # Amounts in minor units (kobo). tax_minor stays 0 until VAT lands (R5-PR2+).
    subtotal_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tax_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # FIRS e-invoicing (deferred): IRN + submission status, populated later.
    irn: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    firs_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    firs_submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invoice_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    voided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    void_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    lines: Mapped[list["InvoiceLine"]] = relationship(
        "InvoiceLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLine.position",
    )

    __table_args__ = (
        UniqueConstraint("org_id", "invoice_number", name="uq_invoice_number"),
    )

    def __repr__(self) -> str:
        return f"<Invoice {self.invoice_number} status={self.status} total={self.total_minor}>"


class InvoiceLine(Base):
    """A single line on an invoice."""

    __tablename__ = "invoice_lines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Nullable until VAT lands — then a tax_code_ref drives tax_minor.
    tax_code_ref: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tax_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    dimension_1: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="lines")

    def __repr__(self) -> str:
        return f"<InvoiceLine {self.description!r} amount={self.amount_minor}>"
