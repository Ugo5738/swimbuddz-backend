"""Dead-letter table for wallet → ledger journal entries that failed to post.

The wallet emitter (services/ledger_emit.py) mirrors every Bubbles movement to
ledger_service. A ledger hiccup must never affect the (already committed) wallet
transaction, but the journal entry can't just be dropped — a lost entry is a
books error. So a failed emit parks its intended entry here; the replay job
(scripts/ledger/replay_ledger_failures.py) re-posts pending rows, and the
ledger's own idempotency_key dedupes any double-send. Symmetric with
payments_service.LedgerPostFailure.
"""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class WalletLedgerPostFailure(Base):
    """A wallet journal entry that couldn't be posted to ledger_service."""

    __tablename__ = "wallet_ledger_post_failures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # The ledger idempotency key (wallet:<txn_type>:<txn_id>) — unique so a
    # repeated failure for the same txn updates one row instead of piling up.
    idempotency_key: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    source_reference: Mapped[Optional[str]] = mapped_column(
        String, index=True, nullable=True
    )
    # Full kwargs for libs.common.ledger_client.post_journal_entry, so replay is
    # a straight re-call.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # pending | replayed | abandoned
    status: Mapped[str] = mapped_column(
        String, default="pending", nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<WalletLedgerPostFailure {self.idempotency_key} "
            f"status={self.status} attempts={self.attempts}>"
        )
