"""The scoped operational review cannot erase verified extra participation."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from scripts.ops import review_vi2_individual_extras as operation
from services.attendance_service.models import AttendanceRecord, AttendanceStatus
from services.sessions_service.models import Session, SessionBooking


@pytest.mark.integration
@pytest.mark.asyncio
async def test_review_preserves_verified_participation_and_replays_safely(
    db_session, monkeypatch
):
    ids, cohort, bello, ajibola = [uuid4(), uuid4()], uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(operation, "SESSION_IDS", [str(x) for x in ids])
    monkeypatch.setattr(operation, "COHORT_ID", str(cohort))
    monkeypatch.setattr(operation, "BELLO", str(bello))
    monkeypatch.setattr(operation, "AJIBOLA", str(ajibola))

    @asynccontextmanager
    async def database():
        yield db_session

    monkeypatch.setattr(operation, "AsyncSessionLocal", database)
    for sid, day in zip(ids, [10, 17]):
        start = datetime(2026, 10, day, 13, tzinfo=timezone.utc)
        db_session.add(
            Session(
                id=sid,
                title="Reviewed extra",
                cohort_id=cohort,
                session_type="cohort_class",
                starts_at=start,
                ends_at=start + timedelta(hours=1),
                cohort_attendance_mode="automatic",
            )
        )
    await db_session.flush()
    protected = SessionBooking(
        session_id=ids[1],
        member_id=ajibola,
        member_auth_id=str(uuid4()),
        status="confirmed",
    )
    db_session.add(protected)
    await db_session.flush()
    for sid, mid in [(ids[0], ajibola), (ids[0], bello), (ids[1], ajibola)]:
        db_session.add(
            AttendanceRecord(
                session_id=sid,
                member_id=mid,
                status="present",
                notes=operation.DEFAULT_NOTE,
            )
        )
    await db_session.commit()
    await operation.review(apply=True, actor_auth_id=str(uuid4()))
    # A repeated apply must preserve exactly the same two verified attendees.
    await operation.review(apply=True, actor_auth_id=str(uuid4()))
    rows = list(
        (
            await db_session.scalars(
                select(AttendanceRecord).where(AttendanceRecord.session_id.in_(ids))
            )
        ).all()
    )
    assert {(row.session_id, row.member_id) for row in rows} == {
        (ids[0], bello),
        (ids[1], ajibola),
    }
    assert all(row.status == AttendanceStatus.PRESENT for row in rows)
    verified = next(row for row in rows if row.member_id == bello)
    assert verified.notes.startswith("Owner-confirmed attendance")
    sessions = (
        await db_session.scalars(select(Session).where(Session.id.in_(ids)))
    ).all()
    assert all(row.cohort_attendance_mode == "individual" for row in sessions)
    assert await db_session.get(SessionBooking, protected.id) is not None
