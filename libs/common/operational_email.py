"""Shared transactional email outbox; Communications owns delivery/migration.

Domain services enqueue in the same database transaction as their change.
They never import Communications models or require its API to be online.
"""

import uuid
import hashlib
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class OperationalEmail(Base):
    __tablename__ = "operational_email_outbox"
    __table_args__ = (Index("ix_operational_email_due", "status", "next_attempt_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    recipient_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    notification_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


async def enqueue_operational_email(
    db,
    *,
    event_key: str,
    subject: str,
    body: str,
    recipient_email: str | None = None,
    member_id: uuid.UUID | None = None,
    html_body: str | None = None,
    notification_body: str | None = None,
    action_url: str | None = None,
    expires_at: datetime | None = None,
) -> None:
    """Idempotently enqueue without committing the caller's transaction."""
    if not recipient_email and not member_id:
        raise ValueError("Email requires a recipient or member")
    if len(event_key) > 255:
        event_key = "sha256:" + hashlib.sha256(event_key.encode()).hexdigest()
    await db.execute(
        insert(OperationalEmail)
        .values(
            id=uuid.uuid4(),
            event_key=event_key,
            recipient_email=recipient_email.strip().lower()
            if recipient_email
            else None,
            member_id=member_id,
            subject=subject[:255],
            body=body,
            notification_body=notification_body,
            html_body=html_body,
            action_url=action_url,
            expires_at=expires_at,
            status="pending",
            attempt_count=0,
            next_attempt_at=utc_now(),
            created_at=utc_now(),
        )
        .on_conflict_do_nothing(index_elements=["event_key"])
    )
