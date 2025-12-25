import uuid
from datetime import datetime

from libs.db.base import Base
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class Member(Base):
    __tablename__ = "members"
    __table_args__ = {"extend_existing": True}

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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Contact & Location
    phone: Mapped[str] = mapped_column(String, nullable=True)
    city: Mapped[str] = mapped_column(String, nullable=True)
    country: Mapped[str] = mapped_column(String, nullable=True)
    time_zone: Mapped[str] = mapped_column(String, nullable=True)

    # Swim Profile
    swim_level: Mapped[str] = mapped_column(String, nullable=True)
    deep_water_comfort: Mapped[str] = mapped_column(String, nullable=True)
    strokes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    interests: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    goals_narrative: Mapped[str] = mapped_column(String, nullable=True)
    goals_other: Mapped[str] = mapped_column(String, nullable=True)

    # Coaching
    certifications: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    coaching_experience: Mapped[str] = mapped_column(String, nullable=True)
    coaching_specialties: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=True
    )
    coaching_years: Mapped[str] = mapped_column(String, nullable=True)
    coaching_portfolio_link: Mapped[str] = mapped_column(String, nullable=True)
    coaching_document_link: Mapped[str] = mapped_column(String, nullable=True)
    coaching_document_file_name: Mapped[str] = mapped_column(String, nullable=True)

    # Logistics
    availability_slots: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    time_of_day_availability: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=True
    )
    location_preference: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    location_preference_other: Mapped[str] = mapped_column(String, nullable=True)
    travel_flexibility: Mapped[str] = mapped_column(String, nullable=True)
    facility_access: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    facility_access_other: Mapped[str] = mapped_column(String, nullable=True)
    equipment_needs: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    equipment_needs_other: Mapped[str] = mapped_column(String, nullable=True)
    travel_notes: Mapped[str] = mapped_column(String, nullable=True)

    # Safety
    emergency_contact_name: Mapped[str] = mapped_column(String, nullable=True)
    emergency_contact_relationship: Mapped[str] = mapped_column(String, nullable=True)
    emergency_contact_phone: Mapped[str] = mapped_column(String, nullable=True)
    emergency_contact_region: Mapped[str] = mapped_column(String, nullable=True)
    medical_info: Mapped[str] = mapped_column(String, nullable=True)
    safety_notes: Mapped[str] = mapped_column(String, nullable=True)

    # Community
    volunteer_interest: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    volunteer_roles_detail: Mapped[str] = mapped_column(String, nullable=True)
    discovery_source: Mapped[str] = mapped_column(String, nullable=True)
    social_instagram: Mapped[str] = mapped_column(String, nullable=True)
    social_linkedin: Mapped[str] = mapped_column(String, nullable=True)
    social_other: Mapped[str] = mapped_column(String, nullable=True)

    # Preferences
    language_preference: Mapped[str] = mapped_column(String, nullable=True)
    comms_preference: Mapped[str] = mapped_column(String, nullable=True)
    payment_readiness: Mapped[str] = mapped_column(String, nullable=True)
    currency_preference: Mapped[str] = mapped_column(String, nullable=True)
    consent_photo: Mapped[str] = mapped_column(String, nullable=True)

    # Membership
    membership_tiers: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    requested_membership_tiers: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=True
    )
    academy_focus_areas: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    academy_focus: Mapped[str] = mapped_column(String, nullable=True)
    payment_notes: Mapped[str] = mapped_column(String, nullable=True)

    # ===== NEW TIER-BASED FIELDS =====
    # Tier Management
    membership_tier: Mapped[str] = mapped_column(
        String, nullable=False, default="community", server_default="community"
    )

    # Profile Photo
    profile_photo_url: Mapped[str] = mapped_column(String, nullable=True)

    # ===== APPROVAL SYSTEM =====
    approval_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )  # pending, approved, rejected
    approval_notes: Mapped[str] = mapped_column(
        String, nullable=True
    )  # Admin vetting notes (visible only to admins)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[str] = mapped_column(
        String, nullable=True
    )  # Email of admin who approved

    # ===== ABOUT YOU (Vetting Questions) =====
    occupation: Mapped[str] = mapped_column(String, nullable=True)  # Work/school
    area_in_lagos: Mapped[str] = mapped_column(
        String, nullable=True
    )  # Which area of Lagos
    how_found_us: Mapped[str] = mapped_column(
        String, nullable=True
    )  # How they found SwimBuddz
    previous_communities: Mapped[str] = mapped_column(
        String, nullable=True
    )  # Sports/fitness community experience
    hopes_from_swimbuddz: Mapped[str] = mapped_column(
        String, nullable=True
    )  # What they hope to gain
    community_rules_accepted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Community Tier - Enhanced fields
    gender: Mapped[str] = mapped_column(String, nullable=True)
    date_of_birth: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    show_in_directory: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    interest_tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)

    # Club Tier - Badges & Tracking
    club_notes: Mapped[str] = mapped_column(String, nullable=True)
    club_badges_earned: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    club_challenges_completed: Mapped[dict] = mapped_column(JSONB, nullable=True)
    punctuality_score: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    commitment_score: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )

    # Academy Tier - Skill Assessment & Goals
    academy_skill_assessment: Mapped[dict] = mapped_column(JSONB, nullable=True)
    academy_goals: Mapped[str] = mapped_column(String, nullable=True)
    academy_preferred_coach_gender: Mapped[str] = mapped_column(String, nullable=True)
    academy_lesson_preference: Mapped[str] = mapped_column(String, nullable=True)
    academy_certifications: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=True
    )
    academy_graduation_dates: Mapped[dict] = mapped_column(JSONB, nullable=True)
    academy_paid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    academy_alumni: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Billing / Access (Foundation + overlays)
    community_paid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    club_paid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    coach_profile: Mapped["CoachProfile"] = relationship(
        "CoachProfile", back_populates="member", uselist=False, lazy="selectin"
    )

    def __repr__(self):
        return f"<Member {self.email}>"


class PendingRegistration(Base):
    __tablename__ = "pending_registrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    profile_data_json: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    # We could add an expiry, but let's keep it simple.

    def __repr__(self):
        return f"<PendingRegistration {self.email}>"


class VolunteerRole(Base):
    """Volunteer roles available for members to express interest in"""

    __tablename__ = "volunteer_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(
        String, nullable=False
    )  # media/logistics/admin/coaching_support/lane_marshal
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    slots_available: Mapped[int] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<VolunteerRole {self.title}>"


class VolunteerInterest(Base):
    """Tracks member interest in volunteer roles"""

    __tablename__ = "volunteer_interests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="interested"
    )  # interested/active/inactive
    notes: Mapped[str] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<VolunteerInterest member={self.member_id} role={self.role_id}>"


class ClubChallenge(Base):
    """Club challenges that members can complete to earn badges"""

    __tablename__ = "club_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=True)
    challenge_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # time_trial/attendance/distance/technique
    badge_name: Mapped[str] = mapped_column(String, nullable=False)
    criteria_json: Mapped[str] = mapped_column(
        String, nullable=True
    )  # JSON stored as string
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self):
        return f"<ClubChallenge {self.title}>"


class MemberChallengeCompletion(Base):
    """Tracks member completion of club challenges"""

    __tablename__ = "member_challenge_completions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    challenge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    result_data: Mapped[str] = mapped_column(
        String, nullable=True
    )  # JSON stored as string
    verified_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # Coach/admin verification

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    def __repr__(self):
        return (
            f"<MemberChallengeCompletion member={self.member_id} "
            f"challenge={self.challenge_id}>"
        )


class CoachProfile(Base):
    """
    Profile for a coach, linked to a Member.
    Allows a user to be both a regular member and a coach.
    """

    __tablename__ = "coach_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), unique=True, nullable=False
    )

    # --- A. Identity (Extends Member) ---
    display_name: Mapped[str] = mapped_column(
        String, nullable=True
    )  # e.g. "Coach Tobi"
    coach_profile_photo_url: Mapped[str] = mapped_column(
        String, nullable=True
    )  # Professional headshot
    short_bio: Mapped[str] = mapped_column(String, nullable=True)  # 1-2 lines
    full_bio: Mapped[str] = mapped_column(Text, nullable=True)  # Detailed

    # --- B. Professional ---
    certifications: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=True
    )  # ["CPR", "ASCA_1"]
    other_certifications_note: Mapped[str] = mapped_column(Text, nullable=True)

    coaching_years: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    coaching_experience_summary: Mapped[str] = mapped_column(Text, nullable=True)

    coaching_specialties: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=True
    )
    levels_taught: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    age_groups_taught: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=True
    )  # ["kids", "teens", "adults"]
    preferred_cohort_types: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=True
    )  # ["group", "one_to_one"]

    languages_spoken: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    coaching_portfolio_link: Mapped[str] = mapped_column(String, nullable=True)

    # --- C. Safety & Compliance ---
    has_cpr_training: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    cpr_expiry_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lifeguard_expiry_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    background_check_status: Mapped[str] = mapped_column(
        String, default="not_required", server_default="not_required"
    )  # pending, verified, rejected
    background_check_document_url: Mapped[str] = mapped_column(String, nullable=True)

    insurance_status: Mapped[str] = mapped_column(
        String, default="none", server_default="none"
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # --- D. Logistics & Commercial ---
    pools_supported: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=True
    )  # Location IDs/Names
    can_travel_between_pools: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    travel_radius_km: Mapped[float] = mapped_column(Float, nullable=True)

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

    # Availability can still be JSON for now until we have a detailed slot model
    availability_calendar: Mapped[dict] = mapped_column(JSONB, nullable=True)

    # Pricing (Nullable for now)
    currency: Mapped[str] = mapped_column(String, default="NGN", server_default="NGN")
    one_to_one_rate_per_hour: Mapped[int] = mapped_column(Integer, nullable=True)
    group_session_rate_per_hour: Mapped[int] = mapped_column(Integer, nullable=True)
    academy_cohort_stipend: Mapped[int] = mapped_column(Integer, nullable=True)

    # --- E. Platform / Ops ---
    status: Mapped[str] = mapped_column(
        String, default="draft", server_default="draft"
    )  # draft, pending_review, more_info_needed, approved, rejected, active, inactive, suspended

    # Application tracking
    application_submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    application_reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    application_reviewed_by: Mapped[str] = mapped_column(
        String, nullable=True
    )  # Email of reviewer
    rejection_reason: Mapped[str] = mapped_column(Text, nullable=True)

    show_in_directory: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    is_featured: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    average_rating: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0.0"
    )
    rating_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    admin_notes: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    member: Mapped["Member"] = relationship(
        "Member", back_populates="coach_profile", uselist=False
    )

    def __repr__(self):
        return f"<CoachProfile {self.member_id} ({self.display_name})>"
