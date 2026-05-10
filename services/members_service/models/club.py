"""Club entity — a structured swimming club within SwimBuddz.

SwimBuddz has three membership tiers (community / club / academy). The
"Club" tier is conceptually a structured training programme; until now it
was implicit. This model makes a Club a first-class entity so:

  * Challenges can scope to a specific club (club_id on club_challenges).
  * Future features (club rosters, club-specific announcements, club-only
    events) can hang off the same model without further migrations.
  * Pods (small Club training sub-groups) inherit their default session
    day/time/duration from their parent Club — see ``models/pod.py`` and
    ``docs/club/POD_OPERATIONS.md``.

Keeping it intentionally small for v1 — name, slug, description, location,
is_active, plus the default session schedule that pods inherit from. A
per-club coach/owner attribution and a roster table can land later when a
use case demands them.
"""

import uuid
from datetime import datetime, time
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, DateTime, Integer, String, Time
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.members_service.models.enums import DayOfWeek, enum_values


class Club(Base):
    """A structured swimming club within SwimBuddz."""

    __tablename__ = "clubs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )  # url-safe identifier; also stable for cross-service refs
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Default session schedule — pods created under this Club inherit these
    # at creation time. The Club default exists so most pods don't have to
    # configure anything; pods that genuinely need a different anchor (e.g.
    # a Wednesday-morning crew) override on a per-pod basis.
    default_session_day: Mapped[DayOfWeek] = mapped_column(
        SAEnum(
            DayOfWeek,
            name="day_of_week_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=DayOfWeek.SAT,
        server_default=DayOfWeek.SAT.value,
    )
    default_session_time: Mapped[time] = mapped_column(
        Time, nullable=False, default=time(9, 0), server_default="09:00"
    )
    default_session_duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=180, server_default="180"
    )
    # Cross-service ref → pools_service.pools.id. Nullable: not every Club
    # has a fixed home pool yet. No FK enforced (different service owner).
    default_pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self) -> str:
        return f"<Club {self.slug}>"
