"""Contract tests for attendance_service internal endpoints.

These tests verify that response shapes match what other services expect.
"""

import pytest
from tests.factories import AttendanceRecordFactory, MemberFactory, SessionFactory


@pytest.mark.asyncio
@pytest.mark.contract
async def test_member_attendance_contract(attendance_client, db_session):
    """Academy service uses this shape for progress tracking."""
    member = MemberFactory.create()
    session = SessionFactory.create()
    db_session.add_all([member, session])
    await db_session.flush()

    record = AttendanceRecordFactory.create(
        session_id=session.id,
        member_id=member.id,
    )
    db_session.add(record)
    await db_session.commit()

    response = await attendance_client.get(f"/internal/attendance/member/{member.id}")
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) >= 1
    required_fields = ["id", "session_id", "member_id", "status"]
    for field in required_fields:
        assert field in data[0], (
            f"Missing contract field '{field}' in attendance response. "
            f"Used by academy_service for progress tracking."
        )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_session_attendee_ids_contract(attendance_client, db_session):
    """Communications service uses member ID lists for notifications."""
    member = MemberFactory.create()
    session = SessionFactory.create()
    db_session.add_all([member, session])
    await db_session.flush()

    record = AttendanceRecordFactory.create(
        session_id=session.id,
        member_id=member.id,
    )
    db_session.add(record)
    await db_session.commit()

    response = await attendance_client.get(
        f"/internal/attendance/session/{session.id}/member-ids"
    )
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert all(isinstance(item, str) for item in data)
