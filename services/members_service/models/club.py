"""Club entity — a structured swimming club within SwimBuddz.

SwimBuddz has three membership tiers (community / club / academy). The
"Club" tier is conceptually a structured training programme; until now it
was implicit. This model makes a Club a first-class entity so:

  * Challenges can scope to a specific club (club_id on club_challenges).
  * Future features (club rosters, club-specific announcements, club-only
    events) can hang off the same model without further migrations.
  * Pods (small Club training sub-groups) inherit their default session
    day/time/duration from their parent Club — see ``models/pod.py`` and
    ``docs/club/POD_OPERATIONS.md``.

Keeping it intentionally small for v1 — name, slug, description, location,
is_active, plus the default session schedule that pods inherit from. A
per-club coach/owner attribution and a roster table can land later when a
use case demands them.
"""

import uuid
from datetime import date, datetime, time
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from services.members_service.models.enums import DayOfWeek, enum_values


class Club(Base):
    """A structured swimming club within SwimBuddz."""

    __tablename__ = "clubs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )  # url-safe identifier; also stable for cross-service refs
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Default session schedule — pods created under this Club inherit these
    # at creation time. The Club default exists so most pods don't have to
    # configure anything; pods that genuinely need a different anchor (e.g.
    # a Wednesday-morning crew) override on a per-pod basis.
    default_session_day: Mapped[DayOfWeek] = mapped_column(
        SAEnum(
            DayOfWeek,
            name="day_of_week_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=DayOfWeek.SAT,
        server_default=DayOfWeek.SAT.value,
    )
    default_session_time: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(9, 0), server_default="09:00"
    )
    default_session_duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=180, server_default="180"
    )
    # Cross-service ref → pools_service.pools.id. Nullable: not every Club
    # has a fixed home pool yet. No FK enforced (different service owner).
    default_pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Cross-service ref -> pools_service.operating_areas.id. Registration uses
    # the area to filter location-specific Club packages; pricing remains on a
    # versioned plan instead of being inferred from geography at checkout.
    operating_area_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self) -> str:
        return f"<Club {self.slug}>"


class ClubPlanVersion(Base):
    """An approved, purchasable Club package for one location and period.

    Pool/refreshment rate catalogues are operational cost inputs. This row is
    the commercial snapshot members actually buy, so a later supplier-rate
    edit never changes an in-flight registration or historical enrollment.
    """

    __tablename__ = "club_plan_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clubs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Immutable location snapshots. A later Club-default edit must not move a
    # published plan, in-flight application, or paid enrollment.
    pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    operating_area_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    billing_cycle: Mapped[str] = mapped_column(
        String(24), nullable=False, default="quarterly"
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="NGN")
    club_fee_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    community_experience_fee_kobo: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3_000_000, server_default="3000000"
    )
    community_experience_default_selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    community_experience_offering_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_experience_offerings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sessions_included: Mapped[int] = mapped_column(
        Integer, nullable=False, default=12, server_default="12"
    )
    # The service period is deliberately separate from effective_from/to.
    # Effective dates version the published price; period dates identify the
    # actual quarter entitlement being purchased.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    minimum_entry_sessions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    refreshments_included: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    capacity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    premium_venue_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint("club_fee_kobo >= 0", name="ck_club_plan_fee_nonnegative"),
        CheckConstraint(
            "community_experience_fee_kobo >= 0",
            name="ck_club_plan_experience_fee_nonnegative",
        ),
        CheckConstraint("sessions_included > 0", name="ck_club_plan_sessions_positive"),
        CheckConstraint(
            "minimum_entry_sessions > 0 AND minimum_entry_sessions <= sessions_included",
            name="ck_club_plan_minimum_entry_sessions",
        ),
        CheckConstraint(
            "period_end >= period_start", name="ck_club_plan_service_period"
        ),
        Index(
            "ix_club_plan_versions_active_period",
            "club_id",
            "is_active",
            "effective_from",
        ),
    )


class CommunityExperienceOffering(Base):
    """Quarter-specific Community Experience with contextual member prices."""

    __tablename__ = "community_experience_offerings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="NGN")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    standard_member_fee_kobo: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5_000_000, server_default="5000000"
    )
    club_member_fee_kobo: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4_000_000, server_default="4000000"
    )
    club_bundle_fee_kobo: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3_000_000, server_default="3000000"
    )
    purchase_opens_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    purchase_closes_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "period_end >= period_start", name="ck_community_experience_period"
        ),
        CheckConstraint(
            "standard_member_fee_kobo >= 0 AND club_member_fee_kobo >= 0 "
            "AND club_bundle_fee_kobo >= 0",
            name="ck_community_experience_fees_nonnegative",
        ),
        CheckConstraint(
            "club_bundle_fee_kobo <= club_member_fee_kobo "
            "AND club_member_fee_kobo <= standard_member_fee_kobo",
            name="ck_community_experience_price_ladder",
        ),
    )


class ClubApplication(Base):
    """A member's location choice and readiness/payment lifecycle."""

    __tablename__ = "club_applications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clubs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    plan_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("club_plan_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="assessment_required",
        server_default="assessment_required",
    )
    community_experience_selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    preferred_pod_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pods.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quote_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        Index("ix_club_applications_member_status", "member_id", "status"),
    )


class ClubApplicationPlan(Base):
    """One independently entitled quarter selected in a Club application."""

    __tablename__ = "club_application_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("club_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("club_plan_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "plan_version_id",
            name="uq_club_application_plan_selection",
        ),
    )


class ClubEnrollmentReservation(Base):
    """Short-lived seat hold created when a Club checkout starts.

    One row is held per selected plan/quarter.  Capacity is therefore
    protected while Paystack is open without granting the member early Club
    access.  Re-starting checkout for the same application refreshes these
    rows instead of creating duplicate holds.
    """

    __tablename__ = "club_enrollment_reservations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("club_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("club_plan_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payment_reference: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default="active"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "plan_version_id",
            name="uq_club_enrollment_reservation_application_plan",
        ),
        CheckConstraint(
            "status IN ('active', 'consumed', 'released')",
            name="ck_club_enrollment_reservation_status",
        ),
        Index(
            "ix_club_enrollment_reservations_live",
            "plan_version_id",
            "status",
            "expires_at",
        ),
    )


class ClubReadinessAssessment(Base):
    """Self-reported pre-screen plus an assessor-owned observed decision."""

    __tablename__ = "club_readiness_assessments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("club_applications.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    self_report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    observed_checks: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    assessor_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    outcome: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    nonstop_distance_m: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deep_water_comfort: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    primary_technique_focus: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_club_milestone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assessor_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_email_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ClubEnrollment(Base):
    """Location-specific Club entitlement created from a paid application."""

    __tablename__ = "club_enrollments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clubs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    plan_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("club_plan_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("club_applications.id", ondelete="RESTRICT"),
        nullable=False,
    )
    payment_reference: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assigned_pod_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pods.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_club_enrollment_period"),
        UniqueConstraint(
            "application_id",
            "plan_version_id",
            name="uq_club_enrollment_application_plan",
        ),
        Index("ix_club_enrollments_member_active", "member_id", "status", "ends_at"),
    )


class CommunityExperiencePurchase(Base):
    """A paid, quarter-specific Community Experience entitlement."""

    __tablename__ = "community_experience_purchases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    offering_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_experience_offerings.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    club_enrollment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("club_enrollments.id", ondelete="SET NULL"),
        nullable=True,
    )
    price_context: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_paid_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_reference: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "member_id", "offering_id", name="uq_community_experience_member_offering"
        ),
        CheckConstraint(
            "amount_paid_kobo >= 0", name="ck_community_experience_purchase_amount"
        ),
    )
