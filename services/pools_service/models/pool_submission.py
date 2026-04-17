"""PoolSubmission model — member-contributed pool suggestions awaiting moderation."""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.pools_service.models.enums import PoolType, enum_values


class PoolSubmissionStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PoolSubmission(Base):
    """A member-submitted pool suggestion.

    Submissions are moderated by admins. Approved submissions are promoted
    to Pool rows (partnership_status=prospect) and the submitter is rewarded
    with Bubbles via an HTTP call to wallet_service (no DB coupling).
    """

    __tablename__ = "pool_submissions"

    # ── Identity ──────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Submitter (from auth token, no FK to other services) ──────────────
    submitter_auth_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    submitter_display_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    submitter_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── Pool details (mirrors minimal Pool fields) ────────────────────────
    pool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    location_area: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pool_type: Mapped[Optional[PoolType]] = mapped_column(
        SAEnum(PoolType, values_callable=enum_values, name="pool_type_enum"),
        nullable=True,
    )
    contact_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── Amenities (flags) ─────────────────────────────────────────────────
    has_changing_rooms: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_showers: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_lockers: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_parking: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    has_lifeguard: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # ── Submitter's experience ────────────────────────────────────────────
    visit_frequency: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # e.g., "weekly", "monthly", "once"
    member_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 1-5
    member_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    photo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ── Moderation ────────────────────────────────────────────────────────
    status: Mapped[PoolSubmissionStatus] = mapped_column(
        SAEnum(
            PoolSubmissionStatus,
            values_callable=enum_values,
            name="pool_submission_status_enum",
        ),
        default=PoolSubmissionStatus.PENDING,
        server_default="pending",
        index=True,
    )
    reviewed_by_auth_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Once approved, links to the created Pool
    promoted_pool_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Reward tracking (no FK — just records what happened)
    reward_granted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    reward_bubbles: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reward_grant_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<PoolSubmission {self.pool_name} by {self.submitter_auth_id} ({self.status})>"
