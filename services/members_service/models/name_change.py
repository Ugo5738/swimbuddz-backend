"""Immutable self-service identity change history."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class MemberNameChange(Base):
    __tablename__ = "member_name_changes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("members.id", ondelete="RESTRICT"), nullable=False, index=True)
    actor_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    old_first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    old_last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    new_first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    new_last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
