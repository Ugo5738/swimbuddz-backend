"""Journal entry and line models — the double-entry core.

Entries are immutable once posted; corrections are reversing entries. Amounts
are ``bigint`` minor units (kobo). The per-entry balance invariant
(sum(debits) == sum(credits)) is enforced by the posting service and a DB
trigger added with the posting engine (PR-2), not by a column CHECK (it spans
rows). The per-line one-sided CHECK lives here.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.ledger_service.models.enums import EntryStatus, enum_values
from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class JournalEntry(Base):
    """A balanced set of journal lines posted as one atomic event."""

    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        nullable=False,
        index=True,
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    posting_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    source_service: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[EntryStatus] = mapped_column(
        SAEnum(
            EntryStatus,
            name="ledger_entry_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=EntryStatus.POSTED,
        nullable=False,
    )
    reversal_of_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_entries.id"), nullable=True
    )
    reversed_by_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_entries.id"), nullable=True
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ledger_periods.id"), nullable=False, index=True
    )
    posted_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ledger_users.id"), nullable=True
    )
    posted_by_service: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    entry_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", lazy="selectin", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "org_id", "idempotency_key", name="uq_journal_entries_org_idem"
        ),
        Index("ix_journal_entries_org_date", "org_id", "entry_date"),
        Index(
            "ix_journal_entries_org_source",
            "org_id",
            "source_service",
            "source_type",
            "source_id",
        ),
    )

    def __repr__(self) -> str:
        return f"<JournalEntry {self.id} {self.source_service}:{self.source_type} ({self.status.value})>"


class JournalLine(Base):
    """One debit or credit line of a journal entry. Exactly one side is non-zero."""

    __tablename__ = "journal_lines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        nullable=False,
        index=True,
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_entries.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chart_of_accounts.id"), nullable=False
    )
    debit_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    credit_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fx_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 8), nullable=True)
    base_debit_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    base_credit_minor: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_centers.id"), nullable=True
    )
    dimension_1: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    dimension_2: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    member_ref: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_ref: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # FK to tax_codes added in the tax phase (table doesn't exist yet) — plain UUID for now.
    tax_code_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    entry: Mapped["JournalEntry"] = relationship(back_populates="lines")

    __table_args__ = (
        CheckConstraint(
            "debit_minor = 0 OR credit_minor = 0",
            name="ck_journal_line_one_sided",
        ),
        CheckConstraint(
            "debit_minor >= 0 AND credit_minor >= 0",
            name="ck_journal_line_non_negative",
        ),
        Index("ix_journal_lines_account_entry", "account_id", "entry_id"),
        Index(
            "ix_journal_lines_cost_center",
            "cost_center_id",
            postgresql_where=text("cost_center_id IS NOT NULL"),
        ),
        Index(
            "ix_journal_lines_member_ref",
            "member_ref",
            postgresql_where=text("member_ref IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        side = (
            f"DR {self.debit_minor}" if self.debit_minor else f"CR {self.credit_minor}"
        )
        return f"<JournalLine {self.id} {side} {self.currency}>"
