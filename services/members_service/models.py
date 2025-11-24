import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from libs.db.base import Base


class Member(Base):
    __tablename__ = "members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    auth_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    
    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    registration_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
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
    coaching_specialties: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    coaching_years: Mapped[str] = mapped_column(String, nullable=True)
    coaching_portfolio_link: Mapped[str] = mapped_column(String, nullable=True)
    coaching_document_link: Mapped[str] = mapped_column(String, nullable=True)
    coaching_document_file_name: Mapped[str] = mapped_column(String, nullable=True)

    # Logistics
    availability_slots: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    time_of_day_availability: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
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

    def __repr__(self):
        return f"<Member {self.email}>"


class PendingRegistration(Base):
    __tablename__ = "pending_registrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    profile_data_json: Mapped[str] = mapped_column(String, nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    # We could add an expiry, but let's keep it simple.

    def __repr__(self):
        return f"<PendingRegistration {self.email}>"
