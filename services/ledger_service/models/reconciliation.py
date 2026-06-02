"""Settlement reconciliation — external PSP transactions vs the books (§11.2).

R3-PR1 posts the *aggregate* settlement drain (DR bank + DR fees / CR clearing).
R3-PR2 proves it at the line level: each PSP settlement transaction is pushed
here as an ``ExternalTransaction`` and matched against ``journal_lines.external_ref``
(our payment reference). A settled transaction with no matching journal entry —
or one whose amount differs — becomes a ``ReconciliationBreak`` for ops to chase.
This is what catches money that hit the bank but never made it into the books
(e.g. a wallet top-up that never posted, or a dead-lettered cash-in).

Both tables are org-keyed; an RLS org-isolation policy is added in a companion
manual migration (mirrors journal_entries et al.).
"""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class ExternalTransaction(Base):
    """A single PSP settlement transaction, ingested for reconciliation."""

    __tablename__ = "external_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        nullable=False,
        index=True,
    )
    psp: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "paystack"
    # The PSP's own immutable transaction id — the idempotency anchor for intake.
    external_txn_id: Mapped[str] = mapped_column(String, nullable=False)
    # The transaction reference — equals our payment.reference (set at charge),
    # so it joins to journal_lines.external_ref. Indexed for the match query.
    external_ref: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    settlement_ref: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    fee_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String, default="NGN", nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    occurred_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Reconciliation state: unmatched | matched | amount_mismatch
    match_status: Mapped[str] = mapped_column(
        String, default="unmatched", nullable=False, index=True
    )
    matched_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("org_id", "psp", "external_txn_id", name="uq_external_txn_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<ExternalTransaction {self.psp}:{self.external_txn_id} "
            f"ref={self.external_ref} status={self.match_status}>"
        )


class ReconciliationBreak(Base):
    """An item that didn't tie out between the PSP and the books."""

    __tablename__ = "reconciliation_breaks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        nullable=False,
        index=True,
    )
    # unmatched_settlement | amount_mismatch | (future: unposted_charge)
    break_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    psp: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_ref: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    external_txn_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    settlement_ref: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # expected = what the books say; actual = what the PSP reported.
    expected_minor: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    actual_minor: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String, default="NGN", nullable=False)
    # open | resolved | ignored
    status: Mapped[str] = mapped_column(
        String, default="open", nullable=False, index=True
    )
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("org_id", "break_type", "external_ref", name="uq_recon_break"),
    )

    def __repr__(self) -> str:
        return (
            f"<ReconciliationBreak {self.break_type} ref={self.external_ref} "
            f"status={self.status}>"
        )
