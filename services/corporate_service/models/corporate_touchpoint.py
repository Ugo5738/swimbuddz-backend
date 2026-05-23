"""CorporateTouchpoint — log of outreach interactions with a corporate contact."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from services.corporate_service.models.corporate_contact import CorporateContact

from services.corporate_service.models.enums import (
    TouchpointDirection,
    TouchpointType,
    enum_values,
)


class CorporateTouchpoint(Base):
    """A single recorded interaction (email, call, demo, etc.) with a contact.

    Phase 1 just logs touchpoints — automation of the outreach sequence comes
    later. Optionally linked to a specific deal (e.g., proposal_shared during
    a particular deal cycle).
    """

    __tablename__ = "corporate_touchpoints"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corporate_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    deal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corporate_deals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    type: Mapped[TouchpointType] = mapped_column(
        SAEnum(
            TouchpointType,
            values_callable=enum_values,
            name="corporate_touchpoint_type_enum",
        ),
        nullable=False,
    )
    direction: Mapped[TouchpointDirection] = mapped_column(
        SAEnum(
            TouchpointDirection,
            values_callable=enum_values,
            name="corporate_touchpoint_direction_enum",
        ),
        default=TouchpointDirection.OUTBOUND,
        server_default="outbound",
        nullable=False,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    summary: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    logged_by_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    contact: Mapped["CorporateContact"] = relationship(
        "CorporateContact", back_populates="touchpoints"
    )

    def __repr__(self) -> str:
        return f"<CorporateTouchpoint {self.type.value} @ {self.occurred_at}>"
