"""CorporateProgram — a won deal turned into an active wellness program."""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from services.corporate_service.models.corporate_contact import CorporateContact
    from services.corporate_service.models.corporate_deal import CorporateDeal
    from services.corporate_service.models.corporate_program_employee import (
        CorporateProgramEmployee,
    )

from services.corporate_service.models.enums import (
    DiscountTier,
    PaymentTerms,
    ProgramStatus,
    enum_values,
)


class CorporateProgram(Base):
    """A sold corporate cohort: bridges sales (CorporateDeal) and delivery
    (academy cohort, wallet, session bookings).

    Cross-service IDs (`cohort_id`, `corporate_wallet_id`) are plain UUIDs —
    no FKs, by architectural rule. The corporate_service does NOT read other
    services' tables; it only stores the ID and calls those services' HTTP
    APIs to act on them.
    """

    __tablename__ = "corporate_programs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Account / deal lineage
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corporate_contacts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    deal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corporate_deals.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ProgramStatus] = mapped_column(
        SAEnum(
            ProgramStatus,
            values_callable=enum_values,
            name="corporate_program_status_enum",
        ),
        default=ProgramStatus.DRAFT,
        server_default="draft",
        nullable=False,
        index=True,
    )

    # Pricing
    employee_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    discount_tier: Mapped[DiscountTier] = mapped_column(
        SAEnum(
            DiscountTier,
            values_callable=enum_values,
            name="corporate_program_discount_tier_enum",
        ),
        default=DiscountTier.FULL_PRICE,
        server_default="full_price",
        nullable=False,
    )
    per_employee_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    total_kobo: Mapped[int] = mapped_column(Integer, nullable=False)

    payment_terms: Mapped[PaymentTerms] = mapped_column(
        SAEnum(
            PaymentTerms,
            values_callable=enum_values,
            name="corporate_program_payment_terms_enum",
        ),
        default=PaymentTerms.DEPOSIT_HALF,
        server_default="deposit_half",
        nullable=False,
    )
    deposit_paid_kobo: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    balance_paid_kobo: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    # Cross-service references (plain UUIDs — see class docstring)
    cohort_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    corporate_wallet_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Schedule
    expected_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    actual_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expected_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    actual_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Pilot partner perks (first 5 corporate customers get the pilot package)
    is_pilot_partner: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    contact: Mapped["CorporateContact"] = relationship(
        "CorporateContact", back_populates="programs"
    )
    deal: Mapped[Optional["CorporateDeal"]] = relationship(
        "CorporateDeal", back_populates="program"
    )
    employees: Mapped[list["CorporateProgramEmployee"]] = relationship(
        "CorporateProgramEmployee",
        back_populates="program",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<CorporateProgram {self.name} ({self.status.value})>"
