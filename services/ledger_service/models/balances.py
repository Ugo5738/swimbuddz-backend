"""Materialized per-(account, period) balance snapshot.

Recomputed from journal_lines on each post (recompute-from-lines, DECIDED §11.3
of the impl plan). Composite primary key (org_id, account_id, period_id).
"""

import uuid
from datetime import datetime

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class AccountBalance(Base):
    """Opening/movement/closing balance for one account in one period."""

    __tablename__ = "account_balances"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        primary_key=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chart_of_accounts.id"),
        primary_key=True,
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_periods.id"),
        primary_key=True,
    )
    opening_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    debits_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    credits_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    closing_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<AccountBalance acct={self.account_id} period={self.period_id} "
            f"closing={self.closing_minor}>"
        )
