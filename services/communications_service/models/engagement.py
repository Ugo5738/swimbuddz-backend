"""Aggregate-only, anonymous engagement counters for educational content."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from libs.db.base import Base
import uuid


class ContentEngagement(Base):
    __tablename__ = "content_engagement"
    __table_args__ = (
        UniqueConstraint("post_id", "event_type", "source", name="uq_content_engagement_bucket"),
        CheckConstraint("total >= 0", name="ck_content_engagement_nonnegative"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("content_posts.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(48), nullable=False, default="website")
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
