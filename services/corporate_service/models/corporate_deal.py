"""CorporateDeal — a sales pipeline opportunity tied to a CorporateContact."""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from services.corporate_service.models.corporate_contact import CorporateContact
    from services.corporate_service.models.corporate_program import CorporateProgram

from services.corporate_service.models.enums import (
    DealLostReason,
    DealStage,
    DiscountTier,
    enum_values,
)


class CorporateDeal(Base):
    """A specific deal/opportunity inside a corporate account.

    Multiple deals per CorporateContact are allowed — e.g., a Q1 pilot and a Q3
    renewal. Each deal becomes (at most) one CorporateProgram when won.
    """

    __tablename__ = "corporate_deals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corporate_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)

    stage: Mapped[DealStage] = mapped_column(
        SAEnum(
            DealStage,
            values_callable=enum_values,
            name="corporate_deal_stage_enum",
        ),
        default=DealStage.LEAD,
        server_default="lead",
        nullable=False,
        index=True,
    )

    expected_employees: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    expected_discount_tier: Mapped[Optional[DiscountTier]] = mapped_column(
        SAEnum(
            DiscountTier,
            values_callable=enum_values,
            name="corporate_discount_tier_enum",
        ),
        nullable=True,
    )
    expected_total_kobo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    expected_close_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    actual_close_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    next_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_action_due: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_touch_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    lost_reason: Mapped[Optional[DealLostReason]] = mapped_column(
        SAEnum(
            DealLostReason,
            values_callable=enum_values,
            name="corporate_deal_lost_reason_enum",
        ),
        nullable=True,
    )
    lost_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    owner_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
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
        "CorporateContact", back_populates="deals"
    )
    program: Mapped[Optional["CorporateProgram"]] = relationship(
        "CorporateProgram", back_populates="deal", uselist=False
    )

    def __repr__(self) -> str:
        return f"<CorporateDeal {self.title} ({self.stage.value})>"
