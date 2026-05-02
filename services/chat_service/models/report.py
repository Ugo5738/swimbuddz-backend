"""Moderation report model.

Members report problematic messages; reports land in a queue consumed by
moderators and safeguarding admins. See design doc §4.1 and §13.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from services.chat_service.models.enums import ReportReason, ReportStatus, enum_values


class ChatMessageReport(Base):
    """A member-filed report about a message."""

    __tablename__ = "chat_message_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    reporter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[ReportReason] = mapped_column(
        SAEnum(
            ReportReason,
            name="chat_report_reason_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[ReportStatus] = mapped_column(
        SAEnum(
            ReportStatus,
            name="chat_report_status_enum",
            values_callable=enum_values,
            validate_strings=True,
        ),
        nullable=False,
        default=ReportStatus.OPEN,
    )
    # Moderator / safeguarding_admin who owns this report. Null = unassigned.
    assigned_to: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        # Primary moderator-queue index: open reports, grouped by reason, oldest first
        Index(
            "ix_chat_message_reports_queue",
            "status",
            "reason",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ChatMessageReport {self.id} message={self.message_id} "
            f"reason={self.reason.value} status={self.status.value}>"
        )
