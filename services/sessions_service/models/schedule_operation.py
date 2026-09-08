"""Service-local audit and idempotency for Club schedule changes."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class ClubScheduleOperation(Base):
    __tablename__ = "club_schedule_operations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    before: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    after: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    notification_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_required"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
