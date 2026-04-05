"""Testimonial model — public quotes shown across landing pages."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class Testimonial(Base):
    """
    A text testimonial from a member. Optionally tagged with one or more
    tracks (academy / club / community / all) so it can be filtered per
    landing page. Rendered publicly, so ONLY publish content with explicit
    member consent.
    """

    __tablename__ = "testimonials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Attribution (display-only snapshot; not a foreign key to protect
    # against member deletion)
    author_name: Mapped[str] = mapped_column(String, nullable=False)
    author_role: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "Academy Graduate", "Club Member", etc.
    author_since: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    author_initials: Mapped[str] = mapped_column(String(4), nullable=False)
    author_photo_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Content
    quote: Mapped[str] = mapped_column(Text, nullable=False)

    # Tracks is stored as a JSON array of strings for forward flexibility.
    # Valid values today: "academy", "club", "community", "all".
    tracks: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )

    # Publication controls
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    # Lower sort_order renders first. Ties break by created_at desc.
    sort_order: Mapped[int] = mapped_column(
        Integer, default=100, nullable=False, server_default="100"
    )

    # Consent tracking — freeform note for internal audit
    consent_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<Testimonial {self.author_name} ({self.id})>"
