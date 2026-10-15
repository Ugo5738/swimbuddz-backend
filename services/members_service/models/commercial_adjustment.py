"""Auditable commercial terms for Club enrollments.

Commercial concessions amend a dated enrollment, never the Club's public rate.
Previously quoted/paid SessionBooking amounts remain immutable.
"""

import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class ClubCommercialAdjustment(Base):
    __tablename__ = "club_commercial_adjustments"

    # Caller-generated, exactly-once idempotency key.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("club_enrollments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_adjustment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    actor_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    original_terms: Mapped[dict] = mapped_column(JSONB, nullable=False)
    approved_terms: Mapped[dict] = mapped_column(JSONB, nullable=False)
    session_fee_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[__import__("datetime").datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_until: Mapped[__import__("datetime").datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[__import__("datetime").datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "session_fee_kobo >= 0",
            name="ck_club_commercial_adjustment_nonnegative",
        ),
        CheckConstraint(
            "valid_until > valid_from",
            name="ck_club_commercial_adjustment_window",
        ),
        Index(
            "ix_club_commercial_adjustments_enrollment_window",
            "enrollment_id",
            "valid_from",
            "valid_until",
        ),
    )
