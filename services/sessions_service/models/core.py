import uuid
from datetime import datetime, time
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Time
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.sessions_service.models.enums import (
    SessionLocation,
    SessionStatus,
    SessionType,
    enum_values,
)

# ============================================================================
# SESSION MODEL
# ============================================================================


class Session(Base):
    """Unified session model for all session types."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # === Type & Status ===
    session_type: Mapped[SessionType] = mapped_column(
        SAEnum(
            SessionType,
            name="session_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=SessionType.CLUB,
        server_default="club",
    )
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(
            SessionStatus,
            name="session_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=SessionStatus.SCHEDULED,
        server_default="scheduled",
    )

    # === Basic Info ===
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # === Timing ===
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(
        String, default="Africa/Lagos", server_default="Africa/Lagos"
    )

    # === Location ===
    # Preferred: pool_id → references a row in the pools registry
    # (pools_service). Since services are decoupled we store it as a
    # plain UUID with no FK constraint; clients resolve the name via
    # the pools API. The legacy `location` enum + `location_name` still
    # exist for backwards compatibility with pre-pool-registry sessions.
    pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    location: Mapped[Optional[SessionLocation]] = mapped_column(
        SAEnum(
            SessionLocation,
            name="session_location_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=True,
    )
    location_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # === Capacity & Fees (stored in kobo — divide by 100 for naira display) ===
    capacity: Mapped[int] = mapped_column(Integer, default=20, server_default="20")
    pool_fee: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ride_share_fee: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # === Context Links (nullable based on session_type) ===
    # For COHORT_CLASS sessions
    cohort_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    # For EVENT sessions
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    # For ONE_ON_ONE / GROUP_BOOKING (future booking system)
    booking_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    # === Cohort-Specific Fields ===
    week_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    lesson_title: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # === Template tracking ===
    template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("session_templates.id"), nullable=True
    )
    is_recurring_instance: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # === Timestamps ===
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # When session was published (DRAFT → SCHEDULED)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # === Relationships ===
    coaches = relationship("SessionCoach", back_populates="session")

    def __repr__(self):
        return f"<Session {self.title} ({self.session_type.value}) at {self.starts_at}>"


class SessionCoach(Base):
    """Junction table: multiple coaches per session."""

    __tablename__ = "session_coaches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False, index=True
    )
    coach_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )  # Member ID of the coach
    role: Mapped[str] = mapped_column(
        String, default="lead", server_default="lead"
    )  # "lead", "assistant"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    session = relationship("Session", back_populates="coaches")

    def __repr__(self):
        return f"<SessionCoach {self.coach_id} ({self.role}) for session {self.session_id}>"


# ============================================================================
# SESSION TEMPLATE MODEL
# ============================================================================


class SessionTemplate(Base):
    """Template for recurring sessions."""

    __tablename__ = "session_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    session_type: Mapped[SessionType] = mapped_column(
        SAEnum(
            SessionType,
            name="session_type_enum",
            values_callable=enum_values,
            validate_strings=True,
            create_type=False,
        ),
        nullable=False,
        default=SessionType.COMMUNITY,
    )

    # Location - string for flexibility (can be predefined or custom)
    location: Mapped[str] = mapped_column(String, nullable=False)

    # Capacity & Fees
    capacity: Mapped[int] = mapped_column(Integer, default=20, server_default="20")
    pool_fee: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ride_share_fee: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Ride Share Configuration (List of ride areas and their settings)
    ride_share_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Recurrence pattern
    day_of_week: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # 0=Monday, 6=Sunday
    start_time: Mapped[time] = mapped_column(
        Time, nullable=False
    )  # Time of day (e.g., 09:00)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    # Auto-generation
    auto_generate: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )  # Auto-create sessions weekly

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<SessionTemplate {self.title}>"


# ============================================================================
# SESSION BUNDLE CART (multi-session booking selection)
# ============================================================================


class SessionBundleCart(Base):
    """Temporary shopping cart for booking multiple sessions at once.

    Created when a member selects multiple sessions from the Sessions Hub.
    The member then navigates to /sessions/bundle/{id}/book which loads
    the selected sessions via this cart. After payment, the cart can be
    kept for analytics or soft-deleted.
    """

    __tablename__ = "session_bundle_carts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # The member (auth id) who created the cart. Used for access control.
    member_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)

    # List of session UUIDs (as strings) selected for the bundle.
    # Validation constraints are enforced at the API layer (max 10, no dups).
    session_ids: Mapped[list] = mapped_column(JSONB, nullable=False)

    # Status: "open" (awaiting payment), "paid", "expired", "abandoned"
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default="open"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default="now()",
    )
    # TTL for abandoned carts — checkout flow should complete within this window.
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<SessionBundleCart {self.id} {len(self.session_ids)} sessions>"
