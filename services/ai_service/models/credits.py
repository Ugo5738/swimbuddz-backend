"""Closed-loop credit ledger for the PUBLIC Stroke Lab analyzer.

Two tables, both INSIDE ai_service (service-isolation: no cross-service tables,
no import of wallet_service — its atomic ledger pattern is REPLICATED here):

  * analyzer_credit_accounts — one row per CANONICAL email (lowercased, +tag and
    Gmail-dot stripped): the running balance + free-tier flag.
  * analyzer_credit_ledger   — append-only history: gumroad_grant / free_grant /
    reserve / consume / refund / revoke, with balance snapshots and an
    idempotency_key that makes every operation exactly-once.

See docs/design/STROKELAB_PUBLIC_ANALYZER_DESIGN.md sections 3b, 3c, 6, 7.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class AnalyzerCreditEntryType(str, enum.Enum):
    """What a ledger row represents."""

    GUMROAD_GRANT = "gumroad_grant"  # paid credits from a Gumroad sale
    FREE_GRANT = "free_grant"  # the 1-per-email free analysis
    RESERVE = "reserve"  # held at submit, pending worker result
    CONSUME = "consume"  # reservation spent on a successful analysis
    REFUND = "refund"  # reservation returned on a failed analysis
    REVOKE = "revoke"  # paid credits clawed back on refund/dispute


class AnalyzerCreditDirection(str, enum.Enum):
    CREDIT = "credit"  # increases remaining_credits
    DEBIT = "debit"  # decreases remaining_credits / reserves


class AnalyzerCreditAccount(Base):
    """Running balance for one canonical email.

    The account row is the contended resource for the "1 free per email" rule:
    submit upserts it (``ON CONFLICT (email) DO NOTHING``) then ``SELECT ... FOR
    UPDATE`` to serialize concurrent first-submits (design sec. 6.2).
    """

    __tablename__ = "analyzer_credit_accounts"
    __table_args__ = (
        CheckConstraint(
            "remaining_credits >= 0", name="ck_analyzer_acct_remaining_nonneg"
        ),
        CheckConstraint(
            "reserved_credits >= 0", name="ck_analyzer_acct_reserved_nonneg"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Canonical email (lowercased, +tag stripped, Gmail dots removed). Unique so
    # one human = one balance and one free analysis.
    email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )

    remaining_credits: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reserved_credits: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    free_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    lifetime_purchased: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lifetime_spent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Set when a Gumroad sale for this email is refunded/disputed. A flagged
    # account loses free-tier re-access (design sec. 7.7) without retaining
    # extra PII.
    flagged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    ledger_entries: Mapped[list["AnalyzerCreditLedger"]] = relationship(
        "AnalyzerCreditLedger",
        back_populates="account",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return (
            f"<AnalyzerCreditAccount email={self.email} "
            f"remaining={self.remaining_credits} reserved={self.reserved_credits}>"
        )


class AnalyzerCreditLedger(Base):
    """Append-only credit history. Every balance change is one row.

    Exactly-once is enforced by ``idempotency_key`` (unique). Gumroad sale dedup
    is additionally enforced by ``gumroad_sale_id`` (unique) on the grant row.
    """

    __tablename__ = "analyzer_credit_ledger"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_analyzer_ledger_amount_pos"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analyzer_credit_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalized canonical email for direct lookup without a join.
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)

    # Natural exactly-once key, e.g. free-{email} / gumroad-sale-{id} /
    # reserve-{job_id} / consume-{job_id} / refund-{job_id} / gumroad-revoke-{id}.
    idempotency_key: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )

    entry_type: Mapped[AnalyzerCreditEntryType] = mapped_column(
        Enum(
            AnalyzerCreditEntryType,
            name="analyzer_credit_entry_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    direction: Mapped[AnalyzerCreditDirection] = mapped_column(
        Enum(
            AnalyzerCreditDirection,
            name="analyzer_credit_direction_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_before: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)

    # 'gumroad' | 'free' | 'system'
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    # Gumroad linkage. gumroad_sale_id is set ONLY on the grant row (unique →
    # sale dedup); the matching revoke leaves it NULL and references the sale via
    # idempotency_key gumroad-revoke-{sale_id} + reversal_of_id.
    gumroad_sale_id: Mapped[Optional[str]] = mapped_column(
        String(120), nullable=True, unique=True
    )
    gumroad_license_key: Mapped[Optional[str]] = mapped_column(
        String(120), nullable=True
    )
    gumroad_permalink: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True
    )

    # The swim_analysis_jobs.id a reserve/consume/refund belongs to. Plain UUID,
    # NO FK — keep it loose so deleting a job doesn't cascade-wipe ledger history.
    job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    # The entry a refund/revoke cancels.
    reversal_of_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    account: Mapped[AnalyzerCreditAccount] = relationship(
        "AnalyzerCreditAccount", back_populates="ledger_entries"
    )

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return (
            f"<AnalyzerCreditLedger {self.entry_type} {self.direction} "
            f"amount={self.amount} key={self.idempotency_key}>"
        )
