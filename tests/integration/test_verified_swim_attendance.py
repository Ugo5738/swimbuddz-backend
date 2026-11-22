from datetime import date
import uuid

import pytest
from sqlalchemy import update

from services.attendance_service.models import AttendanceRecord, AttendanceStatus


@pytest.mark.asyncio
async def test_default_attendance_needs_confirmation_and_corrections_revoke_it(
    db_session,
):
    record = AttendanceRecord(
        session_id=uuid.uuid4(), member_id=uuid.uuid4(), status=AttendanceStatus.PRESENT
    )
    db_session.add(record)
    await db_session.flush()
    await db_session.refresh(record)
    assert record.swim_sync_at is None
    assert record.swim_revision == 0

    record.swim_verified_by = str(uuid.uuid4())
    record.swim_verified_on = date(2026, 10, 5)
    await db_session.flush()
    await db_session.refresh(record)
    assert record.swim_revision == 1
    assert record.swim_sync_at is not None

    # System-written status corrections use bulk SQL in some attendance flows.
    await db_session.execute(
        update(AttendanceRecord)
        .where(AttendanceRecord.id == record.id)
        .values(status=AttendanceStatus.ABSENT)
    )
    await db_session.refresh(record)
    assert record.swim_verified_by is None
    assert record.swim_verified_on is None
    assert record.swim_revision == 2

    record.status = AttendanceStatus.PRESENT
    await db_session.flush()
    await db_session.refresh(record)
    assert record.swim_verified_on is None
    assert record.swim_revision == 2
