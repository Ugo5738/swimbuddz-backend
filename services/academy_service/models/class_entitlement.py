"""Enrollment-owned purchased lesson balances and learner/session allocations."""

import uuid
from datetime import datetime
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class AcademyClassPlan(Base):
    __tablename__ = "academy_class_plans"
    __table_args__ = (
        CheckConstraint(
            "purchased_classes > 0 AND opening_consumed >= 0 AND opening_consumed <= purchased_classes",
            name="ck_academy_class_plan_balance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    purchased_classes: Mapped[int] = mapped_column(Integer, nullable=False)
    opening_consumed: Mapped[int] = mapped_column(Integer, nullable=False)
    opening_as_of: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    baseline_reason: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AcademyClassAllocation(Base):
    __tablename__ = "academy_class_allocations"
    __table_args__ = (
        UniqueConstraint(
            "enrollment_id",
            "session_id",
            name="uq_academy_class_allocations_enrollment_session",
        ),
        CheckConstraint(
            "kind IN ('prepaid','paid_extra','makeup','support')",
            name="ck_academy_class_allocation_kind",
        ),
        CheckConstraint(
            "status IN ('reserved','applied','reversed')",
            name="ck_academy_class_allocation_status",
        ),
        CheckConstraint("agreed_fee_kobo >= 0", name="ck_academy_class_allocation_fee"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    agreed_fee_kobo: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    actor_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
