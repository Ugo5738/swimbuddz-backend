"""Organization model — the multi-tenant root.

Every other ledger table is scoped by ``org_id`` referencing this table. In the
shared SwimBuddz DB this is named ``ledger_organizations`` (not ``organizations``)
to avoid colliding with other services in the same database.
"""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.ledger_service.models.enums import (
    AccountingStandard,
    OrgStatus,
    enum_values,
)
from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class Organization(Base):
    """A tenant. SwimBuddz is org #1; B2B customers are #2..N."""

    __tablename__ = "ledger_organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    legal_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    base_currency: Mapped[str] = mapped_column(String(3), default="NGN", nullable=False)
    fiscal_year_start_month: Mapped[int] = mapped_column(
        SmallInteger, default=1, nullable=False
    )
    accounting_standard: Mapped[AccountingStandard] = mapped_column(
        SAEnum(
            AccountingStandard,
            name="ledger_accounting_standard_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=AccountingStandard.ACCRUAL,
        nullable=False,
    )
    tax_country: Mapped[str] = mapped_column(String(2), default="NG", nullable=False)
    vat_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    firs_taxpayer_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[OrgStatus] = mapped_column(
        SAEnum(
            OrgStatus,
            name="ledger_org_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=OrgStatus.ACTIVE,
        nullable=False,
    )
    org_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<Organization {self.id} {self.name!r} {self.base_currency}>"
