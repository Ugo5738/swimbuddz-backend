# fmt: off
"""Treasury operations: bank-statement evidence, budgets, and requests.

These are operational records, NOT a bank API or an independent general ledger.
An approved request never means that the bank executed it.
"""
import uuid
from datetime import date, datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class TreasuryBankAccount(Base):
    __tablename__ = "treasury_bank_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_organizations.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)  # treasury | operations
    last_four: Mapped[Optional[str]] = mapped_column(String(4))
    # Do not silently remap the existing generic NGN account to a particular bank.
    ledger_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("chart_of_accounts.id"))
    statement_balance_minor: Mapped[Optional[int]] = mapped_column(BigInteger)
    statement_as_of: Mapped[Optional[date]] = mapped_column(Date)
    statement_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    statement_evidence: Mapped[Optional[str]] = mapped_column(String(250))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_treasury_bank_code"),
        CheckConstraint("purpose IN ('treasury','operations')", name="ck_treasury_bank_purpose"),
    )


class TreasuryStatementLine(Base):
    __tablename__ = "treasury_statement_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_organizations.id"), nullable=False, index=True)
    bank_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("treasury_bank_accounts.id"), nullable=False, index=True)
    statement_id: Mapped[str] = mapped_column(String(160), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)  # signed: credit positive, debit negative
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String(180))
    match_status: Mapped[str] = mapped_column(String(30), nullable=False, default="unmatched")
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("journal_entries.id"))
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    imported_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    __table_args__ = (
        UniqueConstraint("org_id", "bank_account_id", "statement_id", "row_number", name="uq_treasury_statement_row"),
        CheckConstraint("amount_minor <> 0", name="ck_treasury_line_nonzero"),
        CheckConstraint("row_number > 0", name="ck_treasury_row_positive"),
        Index("ix_treasury_line_org_date", "org_id", "occurred_on"),
    )


class TreasuryBudget(Base):
    __tablename__ = "treasury_budgets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="expense")
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_treasury_budget_positive"),
        CheckConstraint("kind IN ('expense','funding')", name="ck_treasury_budget_kind"),
        CheckConstraint("ends_on >= starts_on", name="ck_treasury_budget_dates"),
    )


class TreasuryRequest(Base):
    __tablename__ = "treasury_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_organizations.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # expense | funding
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="requested")
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    source_bank_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("treasury_bank_accounts.id"), nullable=False)
    destination_bank_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("treasury_bank_accounts.id"))
    budget_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("treasury_budgets.id"))
    evidence_reference: Mapped[Optional[str]] = mapped_column(String(250))
    invoice_reference: Mapped[Optional[str]] = mapped_column(String(120))
    vendor_name: Mapped[Optional[str]] = mapped_column(String(180))
    programme: Mapped[Optional[str]] = mapped_column(String(80))
    cost_center_code: Mapped[Optional[str]] = mapped_column(String(80))
    service_on: Mapped[Optional[date]] = mapped_column(Date)
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("journal_entries.id"))
    ledger_posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_users.id"), nullable=False)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[Optional[str]] = mapped_column(Text)
    bank_line_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("treasury_statement_lines.id"))
    destination_bank_line_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("treasury_statement_lines.id"))
    confirmed_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_users.id"))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    __table_args__ = (
        CheckConstraint("kind IN ('expense','funding')", name="ck_treasury_request_kind"),
        CheckConstraint("status IN ('requested','approved','rejected','confirmed')", name="ck_treasury_request_status"),
        CheckConstraint("amount_minor > 0", name="ck_treasury_request_positive"),
        Index("ix_treasury_requests_org_status", "org_id", "status"),
    )


class TreasuryAuditEvent(Base):
    __tablename__ = "treasury_audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_organizations.id"), nullable=False, index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

class TreasuryBankSnapshot(Base):
    """Append-only owner-attested statement balances, not live bank API."""
    __tablename__ = "treasury_bank_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_organizations.id"), nullable=False, index=True)
    bank_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("treasury_bank_accounts.id"), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    balance_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(250), nullable=False)
    attested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    __table_args__ = (UniqueConstraint("org_id", "bank_account_id", "as_of", name="uq_treasury_bank_snapshot_day"),)


class TreasuryForecastLine(Base):
    """Planning assumptions; never actual receipts or ledger postings."""
    __tablename__ = "treasury_forecast_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_organizations.id"), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    scenario: Mapped[str] = mapped_column(String(20), nullable=False, default="base")
    effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purpose: Mapped[str] = mapped_column(String(400), nullable=False)
    programme: Mapped[Optional[str]] = mapped_column(String(80))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ledger_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    __table_args__ = (
        CheckConstraint("direction IN ('inflow','outflow')", name="ck_treasury_forecast_direction"),
        CheckConstraint("scenario IN ('base','optimistic','conservative')", name="ck_treasury_forecast_scenario"),
        CheckConstraint("amount_minor > 0", name="ck_treasury_forecast_positive"),
    )
