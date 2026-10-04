"""Commercial rates and canonical participant identities for Sessions.

These models deliberately separate four concerns that used to be overloaded on
`Session`:

* SessionRate answers "what does this audience/access path pay?"
* SessionParticipant answers "which human occupied/attended this session?"

Legacy Session price columns and legacy attendance subject columns remain during
the staged migration. New code prefers these records when present and falls back
to the legacy representation so deployed data can be migrated safely.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class SessionRate(Base):
    """One commercial rate applicable to a session audience/access path."""

    __tablename__ = "session_rates"
    __table_args__ = (
        UniqueConstraint("session_id", "rate_code", name="uq_session_rates_code"),
        CheckConstraint(
            "audience IN ('club','academy','community','guest')",
            name="ck_session_rates_audience",
        ),
        CheckConstraint(
            "rate_mode IN ('included','fixed','calculated')",
            name="ck_session_rates_mode",
        ),
        CheckConstraint(
            "amount_kobo >= 0",
            name="ck_session_rates_amount_nonnegative",
        ),
        Index(
            "ix_session_rates_lookup",
            "session_id",
            "audience",
            "access_source",
            "is_active",
            "priority",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    audience: Mapped[str] = mapped_column(String(24), nullable=False)
    # Optional access-specific refinement. A matching source wins over a
    # generic audience row (NULL), allowing e.g. Club quarter inclusion and
    # transition-per-swim pricing to coexist under audience='club'.
    access_source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    rate_code: Mapped[str] = mapped_column(String(64), nullable=False)
    rate_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="fixed", server_default="fixed"
    )
    amount_kobo: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # legacy_bridge rows are maintained from the old Session columns during
    # the compatibility window. Later admin-authored rows can use "admin".
    source: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="legacy_bridge",
        server_default="legacy_bridge",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    session = relationship("Session", back_populates="rates")


class SessionParticipant(Base):
    """Canonical identity/snapshot for one human associated with a Session."""

    __tablename__ = "session_participants"
    __table_args__ = (
        CheckConstraint(
            "participant_kind IN ('member','guest')",
            name="ck_session_participants_kind",
        ),
        CheckConstraint(
            "source IN ('member_booking','attached_guest','guest_pass','walk_in')",
            name="ck_session_participants_source",
        ),
        CheckConstraint(
            "payment_status IN ('included','unpaid','paid','waived','unknown')",
            name="ck_session_participants_payment_status",
        ),
        CheckConstraint(
            "waiver_status IN ('accepted','missing','not_required','unknown')",
            name="ck_session_participants_waiver_status",
        ),
        CheckConstraint(
            "fee_amount_kobo >= 0",
            name="ck_session_participants_fee_nonnegative",
        ),
        CheckConstraint(
            "(participant_kind = 'member' AND member_id IS NOT NULL) "
            "OR (participant_kind = 'guest' AND member_id IS NULL)",
            name="ck_session_participants_member_kind",
        ),
        UniqueConstraint(
            "session_id", "member_id", name="uq_session_participants_member"
        ),
        UniqueConstraint(
            "session_id",
            "booking_guest_id",
            name="uq_session_participants_booking_guest",
        ),
        UniqueConstraint(
            "session_id", "guest_pass_id", name="uq_session_participants_guest_pass"
        ),
        Index("ix_session_participants_session_source", "session_id", "source"),
        Index("ix_session_participants_phone", "phone_snapshot"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    participant_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)

    member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    booking_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    booking_guest_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    guest_pass_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    converted_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    full_name_snapshot: Mapped[str] = mapped_column(String(160), nullable=False)
    email_snapshot: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    phone_snapshot: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    audience: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    access_source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    rate_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("session_rates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rate_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    fee_amount_kobo: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    payment_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unknown", server_default="unknown"
    )
    payment_method: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    waiver_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unknown", server_default="unknown"
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    session = relationship("Session", back_populates="participants")
