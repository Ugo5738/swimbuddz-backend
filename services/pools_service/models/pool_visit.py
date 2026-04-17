"""PoolVisit — log of visits/interactions the SwimBuddz team has had with a pool."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Date, DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.pools_service.models.enums import PoolVisitType, enum_values


class PoolVisit(Base):
    """A logged visit or interaction with a pool.

    Captures who visited, when, what type, observations, and any follow-up action.
    Serves as the communication/visit history for a pool partnership.
    """

    __tablename__ = "pool_visits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pools.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    visit_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    visit_type: Mapped[PoolVisitType] = mapped_column(
        SAEnum(
            PoolVisitType,
            values_callable=enum_values,
            name="pool_visit_type_enum",
        ),
        nullable=False,
        default=PoolVisitType.SCOUTING,
    )

    # Who from our team — auth_id, not FK (cross-service)
    visitor_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    visitor_display_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Follow-up
    follow_up_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    follow_up_due_at: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    follow_up_completed: Mapped[bool] = mapped_column(
        default=False, server_default="false", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    pool: Mapped["Pool"] = relationship("Pool", back_populates="visits")  # noqa: F821

    def __repr__(self):
        return f"<PoolVisit {self.visit_type.value} at {self.pool_id} on {self.visit_date}>"
