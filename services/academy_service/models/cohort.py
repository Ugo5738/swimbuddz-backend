import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.academy_service.models.enums import (
    CohortStatus,
    CohortType,
    ExtensionRequestStatus,
    LocationType,
    ResourceSourceType,
    ResourceVisibility,
    enum_values,
)

# ============================================================================
# COHORT MODELS
# ============================================================================


class Cohort(Base):
    __tablename__ = "cohorts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "Jan 2026"

    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    capacity: Mapped[int] = mapped_column(Integer, default=10)

    # ── Cohort variant ────────────────────────────────────────────────────
    # A1 Phase 3.2 (2026-05-17). All cohort sessions stay
    # SessionType.COHORT_CLASS — type lives here so the academy can express
    # private 1-on-1, member-specified small groups, and corporate-sponsored
    # cohorts inside the existing cohort framework. See
    # docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §B.
    type: Mapped[CohortType] = mapped_column(
        SAEnum(
            CohortType,
            name="cohort_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=CohortType.GROUP,
        server_default="group",
    )
    # Cross-service ref to a row in the future corporate-wellness model
    # (sponsor / billing / ingest is in scope for a separate design note).
    # Plain UUID, no FK — cross-service rule.
    corporate_program_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Location
    timezone: Mapped[str] = mapped_column(
        String, default="Africa/Lagos", server_default="Africa/Lagos"
    )
    location_type: Mapped[LocationType] = mapped_column(
        SAEnum(
            LocationType,
            name="location_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=LocationType.POOL,
        server_default="pool",
    )
    location_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Preferred location reference — pool in the pools registry.
    # When set, session generation uses this pool for every session in the cohort.
    pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Coach - References Member ID (who has a CoachProfile)
    coach_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Pricing override in kobo (minor NGN unit). Schema auto-converts naira↔kobo.
    price_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Session defaults ─────────────────────────────────────────────────
    # Used when generating sessions for this cohort so admins don't have
    # to set pool_fee / ride configs on every session individually.
    # Schema layer converts naira↔kobo. Ride configs stored as JSON:
    #   [{"ride_area_id": "uuid", "cost_kobo": 500000, "capacity": 4}, ...]
    default_pool_fee: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    default_ride_configs: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    status: Mapped[CohortStatus] = mapped_column(
        SAEnum(
            CohortStatus,
            name="cohort_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=CohortStatus.OPEN,
    )

    allow_mid_entry: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    mid_entry_cutoff_week: Mapped[int] = mapped_column(
        Integer, default=2, nullable=False, server_default="2"
    )
    require_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    # If True, admin must manually approve every dropout instead of it being automatic.
    # When enabled, enrollment moves to DROPOUT_PENDING at missed_count=2 and waits for admin action.
    admin_dropout_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    notes_internal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Installment billing ──────────────────────────────────────────────────
    # When enabled, members can choose to pay in installments at checkout.
    # The admin sets the number of installments and the deposit (first payment).
    # Remaining installments are auto-debited from the member's wallet on schedule.
    installment_plan_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    # Number of total installments (e.g. 3 means deposit + 2 follow-up payments)
    installment_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Deposit amount in kobo. If null, defaults to (total_price / installment_count).
    installment_deposit_amount: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    # Coach grade requirement (derived from complexity score)
    # Values: "grade_1", "grade_2", "grade_3" (stored as strings for compatibility)
    required_coach_grade: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    program = relationship("Program", back_populates="cohorts")
    enrollments = relationship("Enrollment", back_populates="cohort")
    resources = relationship("CohortResource", back_populates="cohort")
    complexity_score = relationship(
        "CohortComplexityScore", back_populates="cohort", uselist=False
    )

    # Coerce string assignments to enum members on Python-side attribute set.
    # SAEnum's bind processor handles enum-member-to-string serialization for
    # the DB write, but doesn't coerce string-to-enum on assignment — so
    # `Cohort(status="open")` would leave the in-memory attribute as a bare
    # string, and a later `cohort.status.value` access would crash. The
    # validator runs on every Python attribute set (NOT on ORM hydration
    # from the DB, which is handled by the SAEnum type itself), so factories
    # and ad-hoc code can pass the lowercase `.value` string without caring
    # whether the receiving code expects an enum.
    @validates("status")
    def _coerce_status(self, key, value):
        if isinstance(value, str):
            if value in CohortStatus.__members__:  # "OPEN"
                return CohortStatus[value]
            try:
                return CohortStatus(value)  # "open"
            except ValueError:
                return value  # let SQLAlchemy raise the proper error
        return value

    @validates("location_type")
    def _coerce_location_type(self, key, value):
        if isinstance(value, str):
            if value in LocationType.__members__:
                return LocationType[value]
            try:
                return LocationType(value)
            except ValueError:
                return value
        return value

    def __repr__(self):
        return f"<Cohort {self.name} ({self.status})>"


class CohortTimelineShiftLog(Base):
    """Immutable audit record for cohort timeline shift executions."""

    __tablename__ = "cohort_timeline_shift_logs"
    __table_args__ = (
        UniqueConstraint(
            "cohort_id",
            "idempotency_key",
            name="uq_cohort_timeline_shift_logs_idempotency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    actor_auth_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    actor_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    old_start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    old_end_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    new_start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    new_end_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    delta_seconds: Mapped[int] = mapped_column(Integer, nullable=False)

    options_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    results_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    warnings: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self):
        return f"<CohortTimelineShiftLog Cohort={self.cohort_id} Created={self.created_at}>"


# ============================================================================
# EXTENSION REQUEST MODELS
# ============================================================================


class CohortExtensionRequest(Base):
    """Coach-initiated request to extend a cohort's end date (max 4 weeks)."""

    __tablename__ = "cohort_extension_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cohorts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    coach_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Number of weeks to extend (1-4)
    weeks_requested: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    current_end_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    proposed_end_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    status: Mapped[ExtensionRequestStatus] = mapped_column(
        SAEnum(
            ExtensionRequestStatus,
            name="extension_request_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=ExtensionRequestStatus.PENDING,
    )

    # Admin review
    reviewed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    cohort = relationship("Cohort")

    def __repr__(self):
        return f"<CohortExtensionRequest Cohort={self.cohort_id} +{self.weeks_requested}w ({self.status})>"


# ============================================================================
# RESOURCE MODELS
# ============================================================================


class CohortResource(Base):
    __tablename__ = "cohort_resources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    resource_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 'note', 'drill', 'assignment'
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Source (URL or upload)
    source_type: Mapped[ResourceSourceType] = mapped_column(
        SAEnum(
            ResourceSourceType,
            name="resource_source_type_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=ResourceSourceType.URL,
        server_default="url",
    )
    content_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items
    storage_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Visibility & Organization
    visibility: Mapped[ResourceVisibility] = mapped_column(
        SAEnum(
            ResourceVisibility,
            name="resource_visibility_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=ResourceVisibility.ENROLLED_ONLY,
        server_default="enrolled_only",
    )
    week_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    cohort = relationship("Cohort", back_populates="resources")
