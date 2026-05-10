"""Pod and PodAssignment models.

A pod is a small (2–5 member) persistent training sub-group inside a
Club. Each pod has a Pod Lead (required), an optional Assistant Pod Lead,
and shares one chat channel. Pods are peer-led — they have no coaches.
Coaches only exist in the Academy layer.

See [docs/club/POD_OPERATIONS.md](../../../../docs/club/POD_OPERATIONS.md)
for the product decisions and field rationale.

Note: this model previously lived in `sessions_service` with `lead_coach_id`
+ `assistant_coach_id`. It moved to `members_service` in May 2026 to match
the conceptual reality (a pod is a member grouping, not an event) and to
remove the coaching language. Sessions service now reads pod data over
HTTP when needed.
"""

import uuid
from datetime import datetime, time
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.common.datetime_utils import utc_now
from libs.db.base import Base

from services.members_service.models.enums import (
    DayOfWeek,
    PodAssignmentSource,
    PodStatus,
    PodVisibility,
    enum_values,
)


class Pod(Base):
    """A Club training sub-group of 2–5 members (configurable, max 10)."""

    __tablename__ = "pods"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Real FK now that pods and clubs live in the same service.
    club_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clubs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Display name. Falls back to slug at the API layer if blank.
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # URL-safe identifier — auto-generated `{club.slug}-pod-{N}` if name
    # blank at creation. Unique per club so two clubs can both have a
    # pod called "tigers".
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    # Optional public "username" (Dolphins, Orcas, Mantas, …). Unique per
    # club. Renders in the WhatsApp group name `SB Club – Dolphins` and
    # the member-facing dashboard. NULL until assigned by an admin.
    handle: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Pod leadership: required Pod Lead, optional Assistant Pod Lead.
    # Both are member ids (FK to members.id). Pods are peer-led; the word
    # "coach" intentionally does not appear here.
    pod_lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assistant_pod_lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Sizing — defaults match the v1 spec; admins can tune per pod within
    # reasonable bounds (validated at the API layer; max enforceable: 10).
    min_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    max_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )

    # Default session schedule — inherited from the parent Club at
    # creation, overridable per-pod. One-off reschedules (this week we
    # swim Sunday) live as one-off sessions in sessions_service; they do
    # NOT mutate these defaults.
    default_session_day: Mapped[DayOfWeek] = mapped_column(
        SAEnum(
            DayOfWeek,
            name="day_of_week_enum",
            values_callable=enum_values,
            validate_strings=True,
            create_type=False,  # Type already created by Club
        ),
        nullable=False,
    )
    default_session_time: Mapped[time] = mapped_column(Time, nullable=False)
    default_session_duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=180, server_default="180"
    )
    # Cross-service ref → pools_service.pools.id. No enforced FK.
    default_pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
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
    # pods to admins.
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
        # Lekki shouldn't collide.
        UniqueConstraint("club_id", "slug", name="uq_pods_club_slug"),
        # Handle-uniqueness scoped per club; only enforced when handle is
        # set (the partial index lives in __table_args__ via Index below).
        Index(
            "uq_pods_club_handle",
            "club_id",
            "handle",
            unique=True,
            postgresql_where="handle IS NOT NULL",
        ),
        # "Show me this club's active pods" — hot query for the directory.
        Index("ix_pods_club_status", "club_id", "status"),
        # Public-directory query: only public + active.
        Index("ix_pods_directory", "visibility", "status"),
        # Review-queue task: pods past their review-due date.
        Index("ix_pods_review_due", "review_due_at"),
    )

    def __repr__(self) -> str:
        return f"<Pod {self.id} club={self.club_id} slug={self.slug!r}>"


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
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
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
    # Admin / Pod Lead who initiated the placement. NULL when `assigned_by
    # == SELF` (the member themselves).
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
