import enum
import uuid
from datetime import date, datetime, time
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import ARRAY, Boolean, Date, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Text, Time
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# ============================================================================
# ENUMS
# ============================================================================


class VolunteerRoleCategory(str, enum.Enum):
    # Session roles
    SESSION_LEAD = "session_lead"
    WARMUP_LEAD = "warmup_lead"
    LANE_MARSHAL = "lane_marshal"
    CHECKIN = "checkin"
    SAFETY = "safety"
    # Community roles
    WELCOME = "welcome"
    RIDE_SHARE = "ride_share"
    MENTOR = "mentor"
    # Content & media roles
    MEDIA = "media"
    GALLERY_SUPPORT = "gallery_support"
    # Events & logistics roles
    EVENTS_LOGISTICS = "events_logistics"
    TRIP_PLANNER = "trip_planner"
    # Academy support roles
    ACADEMY_ASSISTANT = "academy_assistant"
    # Catch-all
    OTHER = "other"


class VolunteerTier(str, enum.Enum):
    TIER_1 = "tier_1"  # Occasional
    TIER_2 = "tier_2"  # Core
    TIER_3 = "tier_3"  # Lead


class OpportunityStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    FILLED = "filled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class OpportunityType(str, enum.Enum):
    OPEN_CLAIM = "open_claim"
    APPROVAL_REQUIRED = "approval_required"


class SlotStatus(str, enum.Enum):
    CLAIMED = "claimed"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    COMPLETED = "completed"


class RecognitionTier(str, enum.Enum):
    BRONZE = "bronze"  # 10h
    SILVER = "silver"  # 50h
    GOLD = "gold"  # 100h


class RewardType(str, enum.Enum):
    DISCOUNTED_SESSION = "discounted_session"
    FREE_MERCH = "free_merch"
    PRIORITY_EVENT = "priority_event"
    MEMBERSHIP_DISCOUNT = "membership_discount"
    CUSTOM = "custom"


# ============================================================================
# MEMBER REFERENCE (soft reference — no cross-service imports)
# ============================================================================


class MemberRef(Base):
    """Reference to shared members table without cross-service imports."""

    __tablename__ = "members"
    __table_args__ = {"extend_existing": True, "info": {"skip_autogenerate": True}}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


# ============================================================================
# MODELS
# ============================================================================


class VolunteerRole(Base):
    """Definition of a volunteer role (Session Lead, Lane Marshal, etc.)."""

    __tablename__ = "volunteer_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[VolunteerRoleCategory] = mapped_column(
        SAEnum(
            VolunteerRoleCategory,
            name="volunteer_role_category",
            create_constraint=False,
        ),
        default=VolunteerRoleCategory.OTHER,
    )
    required_skills: Mapped[Optional[list]] = mapped_column(
        ARRAY(String), nullable=True
    )
    min_tier: Mapped[VolunteerTier] = mapped_column(
        SAEnum(VolunteerTier, name="volunteer_tier", create_constraint=False),
        default=VolunteerTier.TIER_1,
    )
    icon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    opportunities: Mapped[list["VolunteerOpportunity"]] = relationship(
        back_populates="role"
    )

    def __repr__(self) -> str:
        return f"<VolunteerRole {self.title}>"


class VolunteerProfile(Base):
    """Per-member volunteer profile with tier, hours, and reliability."""

    __tablename__ = "volunteer_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    tier: Mapped[VolunteerTier] = mapped_column(
        SAEnum(VolunteerTier, name="volunteer_tier", create_constraint=False),
        default=VolunteerTier.TIER_1,
    )
    tier_override: Mapped[Optional[str]] = mapped_column(
        SAEnum(VolunteerTier, name="volunteer_tier", create_constraint=False),
        nullable=True,
    )
    total_hours: Mapped[float] = mapped_column(Float, default=0.0)
    total_sessions_volunteered: Mapped[int] = mapped_column(Integer, default=0)
    total_no_shows: Mapped[int] = mapped_column(Integer, default=0)
    total_late_cancellations: Mapped[int] = mapped_column(Integer, default=0)
    reliability_score: Mapped[int] = mapped_column(Integer, default=100)
    recognition_tier: Mapped[Optional[str]] = mapped_column(
        SAEnum(RecognitionTier, name="recognition_tier", create_constraint=False),
        nullable=True,
    )
    preferred_roles: Mapped[Optional[list]] = mapped_column(
        ARRAY(String), nullable=True
    )
    available_days: Mapped[Optional[list]] = mapped_column(ARRAY(String), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self) -> str:
        return f"<VolunteerProfile member={self.member_id} tier={self.tier}>"


class VolunteerOpportunity(Base):
    """A posted volunteer need (e.g., '3 volunteers needed Saturday')."""

    __tablename__ = "volunteer_opportunities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("volunteer_roles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    location_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    slots_needed: Mapped[int] = mapped_column(Integer, default=1)
    slots_filled: Mapped[int] = mapped_column(Integer, default=0)
    opportunity_type: Mapped[OpportunityType] = mapped_column(
        SAEnum(OpportunityType, name="opportunity_type", create_constraint=False),
        default=OpportunityType.OPEN_CLAIM,
    )
    status: Mapped[OpportunityStatus] = mapped_column(
        SAEnum(OpportunityStatus, name="opportunity_status", create_constraint=False),
        default=OpportunityStatus.DRAFT,
    )
    min_tier: Mapped[VolunteerTier] = mapped_column(
        SAEnum(VolunteerTier, name="volunteer_tier", create_constraint=False),
        default=VolunteerTier.TIER_1,
    )
    cancellation_deadline_hours: Mapped[int] = mapped_column(Integer, default=24)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    role: Mapped[Optional["VolunteerRole"]] = relationship(
        back_populates="opportunities"
    )
    slots: Mapped[list["VolunteerSlot"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<VolunteerOpportunity {self.title} on {self.date}>"


class VolunteerSlot(Base):
    """A member's claim on a volunteer opportunity."""

    __tablename__ = "volunteer_slots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("volunteer_opportunities.id", ondelete="CASCADE"),
        index=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[SlotStatus] = mapped_column(
        SAEnum(SlotStatus, name="slot_status", create_constraint=False),
        default=SlotStatus.CLAIMED,
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checked_in_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checked_out_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hours_logged: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    member_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        # One slot per member per opportunity
        {"info": {}},
    )

    # Relationships
    opportunity: Mapped["VolunteerOpportunity"] = relationship(back_populates="slots")

    def __repr__(self) -> str:
        return f"<VolunteerSlot member={self.member_id} status={self.status}>"


class VolunteerHoursLog(Base):
    """Immutable audit trail of hours credited to volunteers."""

    __tablename__ = "volunteer_hours_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        index=True,
    )
    slot_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("volunteer_slots.id", ondelete="SET NULL"),
        nullable=True,
    )
    opportunity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("volunteer_opportunities.id", ondelete="SET NULL"),
        nullable=True,
    )
    hours: Mapped[float] = mapped_column(Float, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    role_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("volunteer_roles.id", ondelete="SET NULL"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(
        String(50), default="slot_completion"
    )  # slot_completion, manual_entry, migration
    logged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self) -> str:
        return f"<VolunteerHoursLog member={self.member_id} hours={self.hours}>"


class VolunteerReward(Base):
    """Perks earned and redeemed by volunteers."""

    __tablename__ = "volunteer_rewards"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        index=True,
    )
    reward_type: Mapped[RewardType] = mapped_column(
        SAEnum(RewardType, name="reward_type", create_constraint=False),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trigger_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # hours_milestone, tier_promotion, manual
    trigger_value: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # e.g., "bronze", "tier_2"
    is_redeemed: Mapped[bool] = mapped_column(Boolean, default=False)
    redeemed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    discount_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    discount_amount_ngn: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    granted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self) -> str:
        return f"<VolunteerReward {self.title} member={self.member_id}>"
