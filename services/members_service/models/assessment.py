"""Swim readiness assessment model."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class SwimAssessment(Base):
    """Stores a completed swim readiness assessment.

    Anonymous (non-logged-in) users can take the quiz — ``member_id`` is
    nullable.  The full answer map is persisted so scores can be re-calculated
    later if the algorithm changes.
    """

    __tablename__ = "swim_assessments"
    __table_args__ = {"extend_existing": True}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Nullable — anonymous users won't have a member record
    member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Raw answer data: {question_id: selected_score, ...}
    answers: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Scoring
    total_score: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-100
    raw_score: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Per-dimension breakdown stored as JSON list
    dimension_scores: Mapped[list] = mapped_column(JSONB, nullable=False)

    # Basic analytics / spam prevention (hashed IP, not raw)
    ip_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self) -> str:
        return f"<SwimAssessment {self.id} score={self.total_score} level={self.level}>"
