import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base


class Member(Base):
    __tablename__ = "members"

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
        return f"<MemberChallengeCompletion member={self.member_id} challenge={self.challenge_id}>"
