"""Accounting period model.

Named ``ledger_periods`` in the shared DB (``periods`` is too generic). Entries
reference the period containing their ``entry_date``; close state gates posting.
"""

import uuid
from datetime import date, datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.ledger_service.models.enums import (
    PeriodStatus,
    PeriodType,
    enum_values,
)
from sqlalchemy import Date, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class Period(Base):
    """A fiscal period (month/quarter/year) with an open/closed lifecycle."""

    __tablename__ = "ledger_periods"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger_organizations.id"),
        nullable=False,
        index=True,
    )
    period_name: Mapped[str] = mapped_column(String, nullable=False)  # '2026-05'
    period_type: Mapped[PeriodType] = mapped_column(
        SAEnum(
            PeriodType,
            name="ledger_period_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PeriodStatus] = mapped_column(
        SAEnum(
            PeriodStatus,
            name="ledger_period_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=PeriodStatus.OPEN,
        nullable=False,
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ledger_users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("org_id", "period_name", name="uq_ledger_periods_org_name"),
    )

    def __repr__(self) -> str:
        return f"<Period {self.period_name} ({self.status.value})>"
