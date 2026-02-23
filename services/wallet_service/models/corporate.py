"""Phase 5 — Corporate wallet models (tables created now, logic deferred)."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class CorporateWallet(Base):
    """Corporate wallet for companies funding employee wellness programs."""

    __tablename__ = "corporate_wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )
    company_name: Mapped[str] = mapped_column(String, nullable=False)
    company_email: Mapped[str] = mapped_column(String, nullable=False)
    admin_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    budget_total: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    member_bubble_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    corp_metadata: Mapped[Optional[dict]] = mapped_column(
        "corp_metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<CorporateWallet {self.company_name}>"


class CorporateWalletMember(Base):
    """Links corporate wallets to individual member wallets."""

    __tablename__ = "corporate_wallet_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    corporate_wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    member_wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    bubbles_allocated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    added_by: Mapped[str] = mapped_column(String, nullable=False)

    def __repr__(self) -> str:
        return f"<CorporateWalletMember corp={self.corporate_wallet_id} member={self.member_wallet_id}>"
