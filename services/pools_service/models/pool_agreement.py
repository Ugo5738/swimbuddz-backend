"""PoolAgreement — formal partnership agreements with commercial terms."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.pools_service.models.enums import PoolAgreementStatus, enum_values


class PoolAgreement(Base):
    """A formal partnership agreement with a pool.

    Captures the commercial terms of the partnership: commission, dates,
    exclusivity, minimum commitments, and a reference to the signed document.
    """

    __tablename__ = "pool_agreements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Identity ──────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[PoolAgreementStatus] = mapped_column(
        SAEnum(
            PoolAgreementStatus,
            values_callable=enum_values,
            name="pool_agreement_status_enum",
        ),
        nullable=False,
        default=PoolAgreementStatus.DRAFT,
        index=True,
    )

    # ── Dates ─────────────────────────────────────────────────────────────
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    signed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Commercials ───────────────────────────────────────────────────────
    commission_percentage: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )  # e.g. 15.00 = 15%
    flat_session_rate_ngn: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    min_sessions_per_month: Mapped[Optional[int]] = mapped_column(nullable=True)

    is_exclusive: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # ── Document reference (media_service id or external URL) ─────────────
    signed_doc_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    signed_doc_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    pool: Mapped["Pool"] = relationship("Pool", back_populates="agreements")  # noqa: F821

    def __repr__(self):
        return f"<PoolAgreement {self.title} ({self.status.value}) for pool {self.pool_id}>"
