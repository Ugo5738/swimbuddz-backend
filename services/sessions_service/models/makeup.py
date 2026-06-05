"""MakeupBooking — the individual-learner make-up / reschedule record.

Phase 0 of make-up scheduling. This is the **scheduling + eligibility** layer for
the Missed-Session policy (docs/policy/MISSED_SESSION_AND_MAKEUP_POLICY.md). It is
DISTINCT from ``payments_service.CohortMakeupObligation``, which tracks the
coach-**payout** side of cohort make-ups; the two coexist and are linked via
``obligation_id``. See docs/design/AVAILABILITY_AND_MAKEUP_SCHEDULING_DESIGN.md §6b/§9.

All cross-service references (learner/coach member ids, block_id, session ids,
obligation_id) are plain UUIDs with NO ForeignKey constraints, per the
no-cross-service-FK rule in docs/reference/SERVICE_COMMUNICATION.md. ``*_session_id``
point at the local ``sessions`` table but stay plain UUIDs for parity with the
other refs and trivially-reversible migrations (mirrors SessionBooking.session_id).
"""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.sessions_service.models.enums import (
    MakeupBlockKind,
    MakeupLearnerType,
    MakeupOrigin,
    MakeupStatus,
    enum_values,
)
from sqlalchemy import Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class MakeupBooking(Base):
    """A make-up / reschedule owed to (or requested by) an individual learner."""

    __tablename__ = "makeup_bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # === Who (cross-service member ids — plain UUIDs, no FK) ===
    learner_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    coach_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    learner_type: Mapped[MakeupLearnerType] = mapped_column(
        SAEnum(
            MakeupLearnerType,
            name="makeup_booking_learner_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=MakeupLearnerType.COHORT,
        server_default="cohort",
    )

    # === Block scoping (grace + make-up window reset per block) ===
    # Resolved per learner_type: cohort term (Phase 1) or lesson package
    # (Phase 2). Nullable until block resolution is wired in.
    block_kind: Mapped[Optional[MakeupBlockKind]] = mapped_column(
        SAEnum(
            MakeupBlockKind,
            name="makeup_booking_block_kind_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=True,
    )
    block_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    origin: Mapped[MakeupOrigin] = mapped_column(
        SAEnum(
            MakeupOrigin,
            name="makeup_booking_origin_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )

    # === Sessions (local table refs, kept as plain UUIDs) ===
    original_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )  # the missed / moved session
    scheduled_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )  # the make-up slot, once confirmed

    status: Mapped[MakeupStatus] = mapped_column(
        SAEnum(
            MakeupStatus,
            name="makeup_booking_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=MakeupStatus.REQUESTED,
        server_default="requested",
        index=True,
    )

    # === Policy state (derived from §4) ===
    used_grace: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )  # did this consume the learner's one grace for the block?
    notice_hours_at_request: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # audit of the 24h-notice rule
    hold_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # soft-hold expiry while a learner request awaits confirmation
    spacing_overridden_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # coach who approved a back-to-back exception (plain UUID)

    # Link to payments_service.CohortMakeupObligation (payout side; §9).
    obligation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        # Outstanding-cap check + per-learner listing: open make-ups by learner.
        Index("ix_makeup_bookings_learner_status", "learner_member_id", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<MakeupBooking {self.id} learner={self.learner_member_id} "
            f"coach={self.coach_member_id} status={self.status.value}>"
        )
