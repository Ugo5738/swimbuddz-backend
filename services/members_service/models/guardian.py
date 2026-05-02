"""Guardian-link model.

Links a minor member to one or more adult guardian members. Used by the chat
service to enforce safeguarding rules (e.g., a coach cannot have a 1:1 DM with
a minor — a verified guardian must be in the channel). Will be used by other
surfaces over time (consent, notifications, billing-by-parent, etc.).

See docs/design/CHAT_SERVICE_DESIGN.md §6 for the safeguarding rules that
depend on this.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


def _enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class GuardianRelationship(str, enum.Enum):
    """How a guardian is related to the minor they guard."""

    PARENT = "parent"
    LEGAL_GUARDIAN = "legal_guardian"
    GRANDPARENT = "grandparent"
    OTHER_ADULT = "other_adult"  # Aunt, uncle, adult sibling, etc.


class GuardianLink(Base):
    """A verified link between a minor member and an adult guardian member.

    Design notes:
      * A minor can have multiple guardians (e.g. both parents) — no uniqueness
        on ``minor_member_id`` alone.
      * The same (minor, guardian) pair can only be active once at a time.
        Enforced by a partial unique index on ``is_active = true``.
      * Deactivation is soft (set ``is_active=false``) — preserves history.
      * ``verified_at`` is null until an admin confirms the relationship.
        Safeguarding rules may require verification before granting guardian
        powers (e.g. being added to a coach-minor DM).
    """

    __tablename__ = "guardian_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    minor_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    guardian_member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relationship: Mapped[GuardianRelationship] = mapped_column(
        SAEnum(
            GuardianRelationship,
            name="guardian_relationship_enum",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Null until an admin verifies the relationship. Gates safeguarding-sensitive
    # uses (e.g. entering a coach-minor DM).
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        # A minor cannot be their own guardian
        CheckConstraint(
            "minor_member_id <> guardian_member_id",
            name="ck_guardian_link_distinct_members",
        ),
        # Only one ACTIVE link per (minor, guardian) pair at a time; historical
        # inactive rows are allowed to accumulate.
        Index(
            "uq_guardian_link_active_pair",
            "minor_member_id",
            "guardian_member_id",
            unique=True,
            postgresql_where="is_active = true",
        ),
    )

    def __repr__(self) -> str:
        state = "active" if self.is_active else "inactive"
        verified = "verified" if self.verified_at else "unverified"
        return (
            f"<GuardianLink {self.id} minor={self.minor_member_id} "
            f"guardian={self.guardian_member_id} {state} {verified}>"
        )
