"""Wallet's versioned copy of a confirmed member swim."""

import uuid
from datetime import date

from libs.db.base import Base
from sqlalchemy import BigInteger, Date, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class VerifiedSwim(Base):
    __tablename__ = "wallet_verified_swims"

    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger)
    swim_date: Mapped[date | None] = mapped_column(Date)
    issued: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
