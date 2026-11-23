from datetime import date
import uuid

import pytest
from sqlalchemy import update

from services.attendance_service.models import AttendanceRecord, AttendanceStatus


@pytest.mark.asyncio
async def test_default_attendance_queues_reward_and_exception_retracts_it(db_session):
    record = AttendanceRecord(
        session_id=uuid.uuid4(), member_id=uuid.uuid4(), status=AttendanceStatus.PRESENT
    )
    db_session.add(record)
    await db_session.flush()
    await db_session.refresh(record)
    assert record.swim_verified_on is None  # Pre-session booking never pays early
    assert record.swim_revision == 1
    assert record.swim_sync_at is not None

    # Worker sets reward date once eligible session has ended.
    record.swim_verified_on = date(2026, 11, 22)
    record.swim_verified_by = "automatic_attendance"
    await db_session.flush()
    await db_session.refresh(record)
    assert record.swim_revision == 2

    await db_session.execute(
        update(AttendanceRecord)
        .where(AttendanceRecord.id == record.id)
        .values(status=AttendanceStatus.ABSENT)
    )
    await db_session.refresh(record)
    assert record.swim_verified_on is None
    assert record.swim_verified_by is None
    assert record.swim_revision == 3

    record.status = AttendanceStatus.PRESENT
    await db_session.flush()
    await db_session.refresh(record)
    assert record.swim_verified_on is None
    assert record.swim_revision == 4
