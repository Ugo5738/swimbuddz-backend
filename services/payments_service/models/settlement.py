"""Paystack settlement records — the PSP→bank payout batches that drain
``paystack_clearing`` (design §11, R3).

A Paystack *settlement* is a batch of successful charges Paystack paid into our
bank account, net of its fees. At cash-in we debit ``paystack_clearing`` by the
gross charge amount; that account only drains when the money actually lands in
the bank. Ingesting each settlement lets the ledger post the settlement entry

    DR bank_operating_ngn (net) + DR expense_psp_fees (gross - net)
    CR paystack_clearing      (gross)

which finally clears the clearing account and books the PSP fee expense.

One row per Paystack settlement id → idempotent ingest. ``ledger_posted`` guards
against re-POSTing an already-drained batch (the ledger's idempotency key is the
ultimate backstop). Line-item matching against individual journal entries +
the reconciliation-breaks queue land in R3-PR2.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import BigInteger, Boolean, Date, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class PaystackSettlement(Base):
    """A Paystack settlement batch ingested for ledger reconciliation."""

    __tablename__ = "paystack_settlements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Paystack's own settlement id — unique, the idempotency anchor for ingest.
    paystack_settlement_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String, default="NGN", nullable=False)
    # All amounts in kobo (minor units). gross = what Paystack processed (and
    # what we debited to clearing at cash-in); net = what hit the bank; fees =
    # Paystack's cut. gross - net folds fees + any deductions (refunds/chargebacks
    # Paystack netted off) — reconciled precisely in PR2.
    gross_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    fees_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    net_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    settlement_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True, index=True
    )
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Whether the clearing-drain journal entry has been posted to the ledger.
    ledger_posted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    ledger_posted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<PaystackSettlement {self.paystack_settlement_id} "
            f"status={self.status} net={self.net_minor} posted={self.ledger_posted}>"
        )
