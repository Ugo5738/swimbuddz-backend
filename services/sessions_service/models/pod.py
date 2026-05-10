"""Pod and PodAssignment models.

A pod is a small (2–5 member) persistent training sub-group inside a
Club. Each pod has a single lead coach, optional assistant coach, and
shares one chat channel.

See [docs/design/POD_MODEL_DESIGN.md](../../../../docs/design/POD_MODEL_DESIGN.md)
for the product decisions and field rationale.

Cross-service references (`club_id`, `lead_coach_id`, `assistant_coach_id`,
`member_id`, `created_by`) are stored as bare UUIDs without enforced FKs —
matches the cross-service convention used elsewhere in this codebase.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.sessions_service.models.enums import (
    PodAssignmentSource,
    PodStatus,
    PodVisibility,
    enum_values,
)


class Pod(Base):
    """A Club training sub-group of 2–5 members."""

    __tablename__ = "pods"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Cross-service ref → members_service.clubs.id. Not enforced as FK; we
    # treat the Club table as shared infrastructure (same Postgres database,
    # different service ownership).
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # URL-safe identifier — unique per club so two clubs can both have a
    # pod called "tigers". Generated from `name` at creation; immutable.
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Coaches: lead is required, assistant optional. Both are member ids
    # (cross-service ref → members.id). Coach status itself is verified
    # at the API boundary, not at the DB level.
    lead_coach_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    assistant_coach_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Sizing — defaults match the v1 spec; admins can tune per pod within
    # reasonable bounds (validated at the API layer).
    min_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    max_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )

    visibility: Mapped[PodVisibility] = mapped_column(
        SAEnum(
            PodVisibility,
            name="pod_visibility_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=PodVisibility.PUBLIC,
        server_default=PodVisibility.PUBLIC.value,
    )
    status: Mapped[PodStatus] = mapped_column(
        SAEnum(
            PodStatus,
            name="pod_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=PodStatus.ACTIVE,
        server_default=PodStatus.ACTIVE.value,
    )

    # 3-month review cycle. `cycle_started_at` is bumped on extend; the
    # background task scans `review_due_at <= now()` to surface review-due
    # pods to coaches and admins.
    cycle_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    review_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    dissolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    assignments: Mapped[list["PodAssignment"]] = relationship(
        back_populates="pod",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        # Slug-uniqueness scoped per club — tigers in Yaba and tigers in
        # Festac shouldn't collide.
        UniqueConstraint("club_id", "slug", name="uq_pods_club_slug"),
        # "Show me this club's active pods" — hot query for the directory.
        Index("ix_pods_club_status", "club_id", "status"),
        # Public-directory query: only public + active.
        Index("ix_pods_directory", "visibility", "status"),
        # Review-queue task: pods past their review-due date.
        Index("ix_pods_review_due", "review_due_at"),
    )

    def __repr__(self) -> str:
        return f"<Pod {self.id} club={self.club_id} name={self.name!r}>"


class PodAssignment(Base):
    """Membership of a member in a pod.

    Soft-leave (set `left_at`) preserves history. Composite uniqueness on
    `(member_id) WHERE left_at IS NULL` enforces the rule that a member
    can only be in one active pod at a time."""

    __tablename__ = "pod_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pod_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pods.id", ondelete="CASCADE"),
        nullable=False,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    # Soft-leave only; the row sticks around for audit + the chat-channel
    # reconciliation contract.
    left_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assigned_by: Mapped[PodAssignmentSource] = mapped_column(
        SAEnum(
            PodAssignmentSource,
            name="pod_assignment_source_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    # Admin / coach who initiated the placement. NULL when `assigned_by ==
    # SELF` (the member themselves).
    assigned_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    pod: Mapped[Pod] = relationship(back_populates="assignments")

    __table_args__ = (
        # The cardinality rule: at most one active pod per member.
        Index(
            "uq_pod_assignments_one_active_per_member",
            "member_id",
            unique=True,
            postgresql_where="left_at IS NULL",
        ),
        # Capacity counts: "how many active members in this pod?"
        Index(
            "ix_pod_assignments_active_per_pod",
            "pod_id",
            postgresql_where="left_at IS NULL",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<PodAssignment pod={self.pod_id} member={self.member_id} "
            f"by={self.assigned_by.value}>"
        )
