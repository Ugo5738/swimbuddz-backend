"""CorporateProgramEmployee — manifest entry for an employee in a corporate program."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from services.corporate_service.models.corporate_program import CorporateProgram

from services.corporate_service.models.enums import (
    EmployeeEnrollmentStatus,
    enum_values,
)


class CorporateProgramEmployee(Base):
    """An employee on a corporate program's manifest.

    `member_id` and `member_auth_id` are cross-service IDs (members_service);
    they get set during the `match-members` step once the employee creates a
    SwimBuddz account using the same email. Until then they're nullable.
    """

    __tablename__ = "corporate_program_employees"
    __table_args__ = (
        # An employee appears at most once per program (deduped by email).
        UniqueConstraint(
            "program_id", "email", name="uq_corporate_program_employee_email"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corporate_programs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Cross-service refs to members_service — set after the employee registers.
    # Plain UUIDs / strings, not FKs (no cross-service FK constraints).
    member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    member_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )

    enrollment_status: Mapped[EmployeeEnrollmentStatus] = mapped_column(
        SAEnum(
            EmployeeEnrollmentStatus,
            values_callable=enum_values,
            name="corporate_employee_enrollment_status_enum",
        ),
        default=EmployeeEnrollmentStatus.PENDING,
        server_default="pending",
        nullable=False,
        index=True,
    )

    invitation_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enrolled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    program: Mapped["CorporateProgram"] = relationship(
        "CorporateProgram", back_populates="employees"
    )

    def __repr__(self) -> str:
        return f"<CorporateProgramEmployee {self.email} ({self.enrollment_status.value})>"
