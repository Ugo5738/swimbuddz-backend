"""Coach-specific models: profiles, agreements, handbook versions, and bank accounts."""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from .member import Member


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
