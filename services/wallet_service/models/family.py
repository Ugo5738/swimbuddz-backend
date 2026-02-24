"""Phase 4 — Family wallet model (table created now, logic deferred)."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class FamilyWalletLink(Base):
    """Links wallets in a parent-child relationship for family spending."""

    __tablename__ = "family_wallet_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    parent_wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    child_wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    spending_limit_per_month: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    spent_this_month: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    month_reset_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("parent_wallet_id", "child_wallet_id", name="uq_family_link"),
        CheckConstraint(
            "parent_wallet_id != child_wallet_id",
            name="ck_family_no_self_link",
        ),
    )

    def __repr__(self) -> str:
        return f"<FamilyWalletLink parent={self.parent_wallet_id} child={self.child_wallet_id}>"
