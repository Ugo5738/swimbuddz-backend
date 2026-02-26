import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.academy_service.models.enums import (
    EnrollmentSource,
    EnrollmentStatus,
    InstallmentStatus,
    PaymentStatus,
    enum_values,
)
from sqlalchemy import JSON, Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

# ============================================================================
# ENROLLMENT MODELS
# ============================================================================


class Enrollment(Base):
    __tablename__ = "enrollments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("programs.id"),
        nullable=True,
    )
    cohort_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Auth ID for ownership verification (avoids cross-service calls)
    member_auth_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )

    # User preferences for matching
    preferences: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    status: Mapped[EnrollmentStatus] = mapped_column(
        SAEnum(
            EnrollmentStatus,
            name="enrollment_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=EnrollmentStatus.PENDING_APPROVAL,
    )

    # Payment tracking
    payment_status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(
            PaymentStatus,
            name="academy_payment_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=PaymentStatus.PENDING,
    )
    # Snapshot of enrollment price in kobo.
    price_snapshot_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    currency_snapshot: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_installments: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    paid_installments_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    missed_installments_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    access_suspended: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    # True only when member explicitly selects installment billing at checkout.
    uses_installments: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    # Enrollment tracking
    enrolled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source: Mapped[EnrollmentSource] = mapped_column(
        SAEnum(
            EnrollmentSource,
            name="enrollment_source_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        default=EnrollmentSource.WEB,
        server_default="web",
    )

    # Notification tracking
    reminders_sent: Mapped[list] = mapped_column(JSON, default=[], server_default="[]")

    # Certificate tracking
    certificate_issued_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    certificate_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # Relationships
    program = relationship("Program")
    cohort = relationship("Cohort", back_populates="enrollments")
    installments = relationship(
        "EnrollmentInstallment",
        back_populates="enrollment",
        order_by="EnrollmentInstallment.installment_number",
        cascade="all, delete-orphan",
    )
    progress_records = relationship("StudentProgress", back_populates="enrollment")

    def __repr__(self):
        return f"<Enrollment Member={self.member_id} Cohort={self.cohort_id}>"


class EnrollmentInstallment(Base):
    __tablename__ = "enrollment_installments"
    __table_args__ = (
        UniqueConstraint(
            "enrollment_id",
            "installment_number",
            name="uq_enrollment_installment_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    installment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # Kobo
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[InstallmentStatus] = mapped_column(
        SAEnum(
            InstallmentStatus,
            name="installment_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=InstallmentStatus.PENDING,
        server_default="pending",
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payment_reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    enrollment = relationship("Enrollment", back_populates="installments")

    def __repr__(self):
        return f"<EnrollmentInstallment Enrollment={self.enrollment_id} No={self.installment_number} Status={self.status}>"
