"""Revenue recognition schedule — the deferred-revenue waterfall (design §10).

When a payment books deferred revenue (a credit to a ``deferred_revenue_*``
account), a schedule row records the obligation and its recognition profile.
The recognition job walks active schedules and posts earned revenue over time
(``DR deferred_revenue_* / CR revenue_*``), advancing ``recognized_minor`` until
the obligation is fully recognised.

One schedule per (org, source, deferred account) — the unique constraint makes
schedule creation idempotent whether it fires from a live post or the backfill.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.ledger_service.models.enums import (
    RecognitionMethod,
    RecognitionStatus,
    enum_values,
)
from sqlalchemy import BigInteger, Date, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class RevenueRecognitionSchedule(Base):
    """A deferred-revenue obligation and how it recognises into earned revenue."""

    __tablename__ = "revenue_recognition_schedules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        nullable=False,
        index=True,
    )

    # What created the deferral (the originating payment) — for idempotent
    # creation and traceability back to the source row.
    source_service: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    origin_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_entries.id"), nullable=True
    )

    deferred_account_ref: Mapped[str] = mapped_column(String, nullable=False)
    revenue_account_ref: Mapped[str] = mapped_column(String, nullable=False)
    dimension_1: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    member_ref: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="NGN")

    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recognized_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    method: Mapped[RecognitionMethod] = mapped_column(
        SAEnum(
            RecognitionMethod,
            name="ledger_recognition_method_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[RecognitionStatus] = mapped_column(
        SAEnum(
            RecognitionStatus,
            name="ledger_recognition_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=RecognitionStatus.ACTIVE,
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "source_service",
            "source_type",
            "source_id",
            "deferred_account_ref",
            name="uq_recognition_schedule_source",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<RevenueRecognitionSchedule {self.deferred_account_ref} "
            f"{self.recognized_minor}/{self.total_minor} ({self.status.value})>"
        )
