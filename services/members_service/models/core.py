"""Member service models with decomposed structure.

The Member model is split into focused tables for better organization:
- Member: Core identity and status
- MemberProfile: Personal info, swim profile, social
- MemberEmergencyContact: Emergency contact and medical info
- MemberAvailability: Scheduling and location preferences
- MemberMembership: Tiers, billing, gamification
- MemberPreferences: User settings
- CoachProfile: Coach-specific data (linked to Member)
"""

import uuid
from datetime import date, datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class Member(Base):
    """Core member identity and status.

    This is the main table that other tables reference.
    Only essential fields that are needed for most queries.
    """

    __tablename__ = "members"
    __table_args__ = {"extend_existing": True}

    # Identity
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    auth_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    registration_complete: Mapped[bool] = mapped_column(Boolean, default=False)

    # Multi-role support (member, coach, admin)
    roles: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=["member"], server_default="{member}"
    )

    # Approval workflow
    approval_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )  # pending, approved, rejected
    approval_notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Profile photo
    profile_photo_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    profile: Mapped["MemberProfile"] = relationship(
        "MemberProfile", back_populates="member", uselist=False, lazy="selectin"
    )
    emergency_contact: Mapped["MemberEmergencyContact"] = relationship(
        "MemberEmergencyContact",
        back_populates="member",
        uselist=False,
        lazy="selectin",
    )
    availability: Mapped["MemberAvailability"] = relationship(
        "MemberAvailability", back_populates="member", uselist=False, lazy="selectin"
    )
    membership: Mapped["MemberMembership"] = relationship(
        "MemberMembership", back_populates="member", uselist=False, lazy="selectin"
    )
    preferences: Mapped["MemberPreferences"] = relationship(
        "MemberPreferences", back_populates="member", uselist=False, lazy="selectin"
    )
    coach_profile: Mapped["CoachProfile"] = relationship(
        "CoachProfile", back_populates="member", uselist=False, lazy="selectin"
    )

    def __repr__(self):
        return f"<Member {self.email}>"


class MemberProfile(Base):
    """Personal information, swim profile, and social links.

    Loaded on demand for profile pages, not needed for most queries.
    """

    __tablename__ = "member_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # Contact
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    time_zone: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Demographics
    gender: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    date_of_birth: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    occupation: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    area_in_lagos: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Swim Profile
    swim_level: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    deep_water_comfort: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    strokes: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), nullable=True)
    interests: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String), nullable=True)
    personal_goals: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Discovery
    how_found_us: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    previous_communities: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    hopes_from_swimbuddz: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Social
    social_instagram: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    social_linkedin: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    social_other: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Directory
    show_in_directory: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    interest_tags: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    member: Mapped["Member"] = relationship("Member", back_populates="profile")

    def __repr__(self):
        return f"<MemberProfile member_id={self.member_id}>"


class MemberEmergencyContact(Base):
    """Emergency contact and medical information.

    Critical safety info, only accessed during incidents.
    """

    __tablename__ = "member_emergency_contacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # Emergency Contact
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    contact_relationship: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Medical
    medical_info: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    safety_notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    # Relationships
    member: Mapped["Member"] = relationship(
        "Member", back_populates="emergency_contact"
    )

    def __repr__(self):
        return f"<MemberEmergencyContact member_id={self.member_id}>"


class MemberAvailability(Base):
    """Scheduling and location preferences.

    Used for session matching and logistics.
    """

    __tablename__ = "member_availabilities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # Availability
    available_days: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    preferred_times: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )

    # Location
    preferred_locations: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    accessible_facilities: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    travel_flexibility: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Equipment
    equipment_needed: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    member: Mapped["Member"] = relationship("Member", back_populates="availability")

    def __repr__(self):
        return f"<MemberAvailability member_id={self.member_id}>"


class MemberMembership(Base):
    """Membership tiers, billing, and gamification.

    Used for access control and payments.
    """

    __tablename__ = "member_memberships"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # Tier Management
    primary_tier: Mapped[str] = mapped_column(
        String, nullable=False, default="community", server_default="community"
    )
    active_tiers: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    requested_tiers: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )

    # Billing
    community_paid_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    club_paid_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    academy_paid_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Pending payment tracking for cross-device resumption
    pending_payment_reference: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, index=True
    )

    # Club Gamification
    club_badges_earned: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    club_challenges_completed: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )
    punctuality_score: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    commitment_score: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    club_notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Academy
    academy_skill_assessment: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )
    academy_goals: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    academy_preferred_coach_gender: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    academy_lesson_preference: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    academy_certifications: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    academy_graduation_dates: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )
    academy_alumni: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    academy_focus_areas: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    member: Mapped["Member"] = relationship("Member", back_populates="membership")

    def __repr__(self):
        return f"<MemberMembership member_id={self.member_id} tier={self.primary_tier}>"


class MemberPreferences(Base):
    """User settings and preferences.

    Rarely accessed, only for settings pages.
    """

    __tablename__ = "member_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # Preferences
    language_preference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    comms_preference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payment_readiness: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    currency_preference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    consent_photo: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    community_rules_accepted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Volunteer
    volunteer_interest: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    volunteer_roles_detail: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    discovery_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    member: Mapped["Member"] = relationship("Member", back_populates="preferences")

    def __repr__(self):
        return f"<MemberPreferences member_id={self.member_id}>"


# ============================================================================
# LEGACY MODELS (not part of Member decomposition)
# ============================================================================


class PendingRegistration(Base):
    """Temporary storage for registration data before email confirmation."""

    __tablename__ = "pending_registrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    profile_data_json: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return f"<PendingRegistration {self.email}>"


class VolunteerRole(Base):
    """LEGACY: Volunteer roles — migrated to volunteer_service.

    Table renamed to legacy_volunteer_roles. Kept here temporarily so
    the data migration script can read from it. Will be removed after
    prod migration is confirmed.
    """

    __tablename__ = "legacy_volunteer_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(
        String, nullable=False
    )  # media/logistics/admin/coaching_support/lane_marshal
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    slots_available: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<VolunteerRole {self.title}>"


class VolunteerInterest(Base):
    """LEGACY: Volunteer interests — migrated to volunteer_service.

    Table renamed to legacy_volunteer_interests. Kept here temporarily so
    the data migration script can read from it. Will be removed after
    prod migration is confirmed.
    """

    __tablename__ = "legacy_volunteer_interests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="interested"
    )  # interested/active/inactive
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<VolunteerInterest member={self.member_id} role={self.role_id}>"


class ClubChallenge(Base):
    """Club challenges that members can complete to earn badges."""

    __tablename__ = "club_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    challenge_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # time_trial/attendance/distance/technique
    badge_name: Mapped[str] = mapped_column(String, nullable=False)
    criteria_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<ClubChallenge {self.title}>"


class MemberChallengeCompletion(Base):
    """Tracks member completion of club challenges."""

    __tablename__ = "member_challenge_completions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    challenge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    result_data: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    verified_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return f"<MemberChallengeCompletion member={self.member_id} challenge={self.challenge_id}>"


class CoachProfile(Base):
    """Coach-specific profile data, linked to a Member."""

    __tablename__ = "coach_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), unique=True, nullable=False
    )

    display_name: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # e.g. "Coach Tobi"
    coach_profile_photo_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items
    short_bio: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    full_bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Professional
    certifications: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    other_certifications_note: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    coaching_years: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    coaching_experience_summary: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    coaching_specialties: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    levels_taught: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    age_groups_taught: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    preferred_cohort_types: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    languages_spoken: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    coaching_portfolio_link: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    coaching_document_link: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    coaching_document_file_name: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )

    # -------------------------------------------------------------------------
    # Coach Grades by Category (for cohort assignment eligibility)
    # Each category has its own grade - coach can have different proficiency levels
    # Grades are assigned by admin based on credentials, experience, and assessments
    # -------------------------------------------------------------------------
    # Grade values: "grade_1", "grade_2", "grade_3" (stored as strings for compatibility)
    learn_to_swim_grade: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    special_populations_grade: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    institutional_grade: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    competitive_elite_grade: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    certifications_grade: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    specialized_disciplines_grade: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    adjacent_services_grade: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )

    # -------------------------------------------------------------------------
    # Progression Tracking
    # -------------------------------------------------------------------------
    total_coaching_hours: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    cohorts_completed: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    average_feedback_rating: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    swimbuddz_level: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # Internal certification level (1, 2, 3)
    last_active_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # -------------------------------------------------------------------------
    # Additional Credential Tracking (expiry dates beyond CPR)
    # -------------------------------------------------------------------------
    first_aid_cert_expiry: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Safety & Compliance
    has_cpr_training: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    cpr_expiry_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lifeguard_expiry_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    background_check_status: Mapped[str] = mapped_column(
        String, default="not_required", server_default="not_required"
    )
    background_check_document_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # FK to media_service.media_items
    insurance_status: Mapped[str] = mapped_column(
        String, default="none", server_default="none"
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Logistics
    pools_supported: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )
    can_travel_between_pools: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    travel_radius_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_swimmers_per_session: Mapped[int] = mapped_column(
        Integer, default=10, server_default="10"
    )
    max_cohorts_at_once: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1"
    )
    accepts_one_on_one: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    accepts_group_cohorts: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    availability_calendar: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Pricing
    currency: Mapped[str] = mapped_column(String, default="NGN", server_default="NGN")
    one_to_one_rate_per_hour: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    group_session_rate_per_hour: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    academy_cohort_stipend: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    # Platform Status
    status: Mapped[str] = mapped_column(String, default="draft", server_default="draft")
    application_submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    application_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    application_reviewed_by: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    show_in_directory: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    is_featured: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    average_rating: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0.0"
    )
    rating_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    admin_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    member: Mapped["Member"] = relationship(
        "Member", back_populates="coach_profile", uselist=False
    )

    def __repr__(self):
        return f"<CoachProfile {self.member_id} ({self.display_name})>"


class CoachAgreement(Base):
    """Coach agreement signature and versioning.

    Tracks signed agreements between coaches and SwimBuddz.
    Each time the agreement text is updated, new signatures are required.
    Previous agreements are superseded but preserved for audit.
    """

    __tablename__ = "coach_agreements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    coach_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coach_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Agreement version and content
    agreement_version: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # e.g., "1.0", "1.1", "2.0"
    agreement_content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # SHA-256 hash of agreement text at signing time

    # Signature details
    signature_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "typed_name", "drawn", "checkbox", "uploaded_image"
    signature_data: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # Typed name string, base64 drawing, "CHECKBOX_AGREE:<timestamp>", or media reference
    signature_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # For uploaded signature images via media service
    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    # Handbook acknowledgment (must be True before signing)
    handbook_acknowledged: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    handbook_version: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # Which handbook version was acknowledged

    # Client metadata for audit trail
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45), nullable=True
    )  # IPv6 max length
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Status and supersession
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    superseded_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coach_agreements.id", ondelete="SET NULL"),
        nullable=True,
    )
    superseded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    coach_profile: Mapped["CoachProfile"] = relationship(
        "CoachProfile", foreign_keys=[coach_profile_id]
    )
    superseded_by: Mapped[Optional["CoachAgreement"]] = relationship(
        "CoachAgreement", remote_side=[id], foreign_keys=[superseded_by_id]
    )

    def __repr__(self):
        return f"<CoachAgreement {self.id} v{self.agreement_version} active={self.is_active}>"


class AgreementVersion(Base):
    """Stores agreement text versions for coach agreements.

    Only one version can be current at a time (is_current=True).
    When a new version is created, the previous one is deactivated.
    Coaches must sign the current version to maintain dashboard access.
    """

    __tablename__ = "agreement_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False
    )  # e.g., "1.0", "2.0"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # Markdown content
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # SHA-256 hash, computed on save
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # Admin who created this version

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<AgreementVersion v{self.version} current={self.is_current}>"


class HandbookVersion(Base):
    """Stores handbook text versions for the coach handbook.

    Only one version can be current at a time (is_current=True).
    Coaches must acknowledge the current handbook version before
    signing the coach agreement.
    """

    __tablename__ = "handbook_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False
    )  # e.g., "1.0", "2.0"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # Markdown content
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # SHA-256 hash, computed on save
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # Admin who created this version

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<HandbookVersion v{self.version} current={self.is_current}>"


class CoachBankAccount(Base):
    """Coach bank account for payouts.

    Stores verified bank account details and Paystack recipient code
    for automated transfers.
    """

    __tablename__ = "coach_bank_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), unique=True, nullable=False
    )

    # Bank details (Nigerian NUBAN format)
    bank_code: Mapped[str] = mapped_column(String(10), nullable=False)  # e.g., "058"
    bank_name: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # e.g., "GTBank"
    account_number: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # 10-digit NUBAN
    account_name: Mapped[str] = mapped_column(
        String(200), nullable=False
    )  # Verified holder name

    # Paystack integration
    paystack_recipient_code: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # Created via Transfer Recipient API

    # Verification status
    is_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_by: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # Admin who verified or "paystack_api"

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<CoachBankAccount {self.account_number} ({self.bank_name})>"
