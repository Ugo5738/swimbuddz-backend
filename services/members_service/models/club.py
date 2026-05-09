"""Club entity — a structured swimming club within SwimBuddz.

SwimBuddz has three membership tiers (community / club / academy). The
"Club" tier is conceptually a structured training programme; until now it
was implicit. This model makes a Club a first-class entity so:

  * Challenges can scope to a specific club (club_id on club_challenges).
  * Future features (club rosters, club-specific announcements, club-only
    events) can hang off the same model without further migrations.

Keeping it intentionally small for v1 — name, slug, description, location,
is_active. Per-club coach/owner attribution and a roster table can land
later when a use case demands them.
"""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self) -> str:
        return f"<Club {self.slug}>"
