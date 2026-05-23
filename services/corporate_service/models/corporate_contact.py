"""CorporateContact — a company / HR contact in the sales pipeline."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from services.corporate_service.models.corporate_deal import CorporateDeal
    from services.corporate_service.models.corporate_program import CorporateProgram
    from services.corporate_service.models.corporate_touchpoint import (
        CorporateTouchpoint,
    )

from services.corporate_service.models.enums import (
    CompanyIndustry,
    CompanySize,
    ContactSource,
    enum_values,
)


class CorporateContact(Base):
    """A corporate buyer / account in the SwimBuddz wellness pipeline."""

    __tablename__ = "corporate_contacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Company
    company_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company_website: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    industry: Mapped[Optional[CompanyIndustry]] = mapped_column(
        SAEnum(
            CompanyIndustry,
            values_callable=enum_values,
            name="corporate_company_industry_enum",
        ),
        nullable=True,
    )
    company_size: Mapped[Optional[CompanySize]] = mapped_column(
        SAEnum(
            CompanySize,
            values_callable=enum_values,
            name="corporate_company_size_enum",
        ),
        nullable=True,
    )
    hq_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Primary HR / wellness contact (single contact in Phase 1; expand to many if needed)
    primary_contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    primary_contact_role: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )  # e.g. "Head of People", "Wellness Manager"
    primary_contact_email: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    primary_contact_phone: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )
    primary_contact_whatsapp: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )

    # Pipeline meta
    source: Mapped[ContactSource] = mapped_column(
        SAEnum(
            ContactSource,
            values_callable=enum_values,
            name="corporate_contact_source_enum",
        ),
        default=ContactSource.COLD_OUTBOUND,
        server_default="cold_outbound",
        nullable=False,
    )
    owner_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )  # admin user who owns this account

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    # Automated outreach — pauses the email scheduler for this contact.
    # ``outreach_started_at`` marks when the admin kicked off the 3-email
    # sequence; when null, the contact is dormant from the scheduler's
    # perspective even if not paused. The scheduler decides "next email
    # is due" by looking at the most recent EMAIL_* touchpoint, so we
    # don't need a separate counter here.
    outreach_paused: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    outreach_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    deals: Mapped[list["CorporateDeal"]] = relationship(
        "CorporateDeal", back_populates="contact", cascade="all, delete-orphan"
    )
    programs: Mapped[list["CorporateProgram"]] = relationship(
        "CorporateProgram", back_populates="contact", cascade="all, delete-orphan"
    )
    touchpoints: Mapped[list["CorporateTouchpoint"]] = relationship(
        "CorporateTouchpoint",
        back_populates="contact",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<CorporateContact {self.company_name}>"
