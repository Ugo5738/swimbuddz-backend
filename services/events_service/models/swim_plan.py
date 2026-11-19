"""Member swim plans and a policy owned by Events, distinct from reservations.

A plan is a *request*, never a PoolAccessBooking, SessionBooking, coach
assignment, paid ticket, or guaranteed facility allocation. Linked Events
are created only after the plan is approved and the venue is verified.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class SwimPublicationPolicy(Base):
    __tablename__ = "swim_publication_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # Default: admin-approved automatically. Admin can require manual review.
    auto_approve_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class SwimPlan(Base):
    """Member intention with explicit approval and independent resource status."""

    __tablename__ = "swim_plans"
    __table_args__ = (
        CheckConstraint("participants BETWEEN 1 AND 25", name="ck_swim_plans_participants"),
        CheckConstraint("ends_at > starts_at", name="ck_swim_plans_time_order"),
        CheckConstraint(
            "kind IN ('solo','friends','open','coached')",
            name="ck_swim_plans_kind",
        ),
        CheckConstraint(
            "approval_status IN ('approved','pending','rejected')",
            name="ck_swim_plans_approval",
        ),
        CheckConstraint(
            "venue_status IN ('pending','verified','not_required','rejected')",
            name="ck_swim_plans_venue",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(1000))
    pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    location: Mapped[Optional[str]] = mapped_column(String(255))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    participants: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    coaching_focus: Mapped[Optional[str]] = mapped_column(String(250))
    approval_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="approved"
    )
    venue_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending_availability"
    )
    linked_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL")
    )
    admin_notes: Mapped[Optional[str]] = mapped_column(String(1000))
    verified_by: Mapped[Optional[str]] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class SwimPlanAudit(Base):
    __tablename__ = "swim_plan_audit"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("swim_plans.id", ondelete="RESTRICT"), nullable=True
    )
    actor_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(String(1000))
    new_value: Mapped[Optional[str]] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
