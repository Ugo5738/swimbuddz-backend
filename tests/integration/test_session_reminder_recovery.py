from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from libs.common.datetime_utils import utc_now
from services.communications_service.models import (
    ScheduledNotification,
    ScheduledNotificationStatus,
    SessionNotificationType,
)
from services.communications_service.tasks.session_notifications import (
    _upsert_session_reminders,
)


@pytest.mark.asyncio
async def test_repeated_reminder_recovery_inserts_one_job_per_type(db_session):
    session_id = uuid4()
    due = utc_now() + timedelta(days=1)
    for _ in range(2):
        candidates = [
            ScheduledNotification(
                session_id=session_id,
                notification_type=kind,
                scheduled_for=due,
                status=ScheduledNotificationStatus.PENDING,
            )
            for kind in [
                SessionNotificationType.REMINDER_24H,
                SessionNotificationType.REMINDER_3H,
            ]
        ]
        await _upsert_session_reminders(db_session, session_id, candidates)
        await db_session.commit()
    rows = (
        (
            await db_session.execute(
                select(ScheduledNotification).where(
                    ScheduledNotification.session_id == session_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
