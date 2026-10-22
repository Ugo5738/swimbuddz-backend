"""Retry operational email delivery independently of domain requests."""

import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from libs.common.datetime_utils import utc_now
from libs.common.emails.core import send_email
from libs.common.logging import get_logger
from libs.common.operational_email import OperationalEmail
from libs.common.service_client import get_members_bulk
from libs.db.config import AsyncSessionLocal
from services.communications_service.models import Notification

logger = get_logger(__name__)


async def deliver_operational_emails(db) -> dict[str, int]:
    sent = failed = expired = 0
    started = time.monotonic()
    for _ in range(10):
        # Lock one recipient until its outcome commits. Concurrent workers skip
        # that row, while completed deliveries survive a later batch timeout.
        row = (
            await db.execute(
                select(OperationalEmail)
                .where(
                    OperationalEmail.status.in_(["pending", "failed"]),
                    OperationalEmail.next_attempt_at <= utc_now(),
                )
                .order_by(OperationalEmail.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if row is None:
            break
        if row.expires_at and row.expires_at <= utc_now():
            row.status = "cancelled"
            expired += 1
            await db.commit()
            continue
        row.attempt_count = (row.attempt_count or 0) + 1
        if row.member_id:
            # Stable notification ID prevents duplicate in-app alerts on retry.
            await db.execute(
                insert(Notification)
                .values(
                    id=row.id,
                    member_id=row.member_id,
                    type="club_operational_update",
                    category="sessions",
                    title=row.subject,
                    body=row.notification_body or row.body,
                    action_url=row.action_url,
                    icon="users",
                    created_at=utc_now(),
                    expires_at=row.expires_at,
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
        try:
            recipient = row.recipient_email
            if not recipient:
                members = await get_members_bulk(
                    [str(row.member_id)], calling_service="communications"
                )
                recipient = next(
                    (
                        m.get("email")
                        for m in members
                        if str(m.get("id")) == str(row.member_id)
                    ),
                    None,
                )
                if not recipient:
                    raise RuntimeError("Member email could not be resolved")
                row.recipient_email = recipient
            success = await send_email(
                to_email=recipient,
                subject=row.subject,
                body=row.body,
                html_body=row.html_body,
            )
            if not success:
                raise RuntimeError("Email provider returned failure")
            row.status = "sent"
            row.sent_at = utc_now()
            row.last_error = None
            sent += 1
        except Exception as exc:
            row.status = "failed"
            row.last_error = str(exc)[:1000]
            row.next_attempt_at = utc_now() + timedelta(
                minutes=min(2 ** min(row.attempt_count - 1, 6), 60)
            )
            failed += 1
            logger.warning(
                "Operational email %s failed (attempt %s)", row.id, row.attempt_count
            )
        await db.commit()
        if time.monotonic() - started > 120:
            break
    return {"sent": sent, "failed": failed, "expired": expired}


async def send_pending_operational_emails() -> dict[str, int]:
    async with AsyncSessionLocal() as db:
        return await deliver_operational_emails(db)
