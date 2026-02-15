"""Integration tests for attendance_service internal endpoints."""

import pytest
from tests.factories import AttendanceRecordFactory, MemberFactory, SessionFactory

# ---------------------------------------------------------------------------
# GET /internal/attendance/member/{member_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_attendance(attendance_client, db_session):
    """Returns attendance records for a specific member."""
    member = MemberFactory.create()
    session = SessionFactory.create()
    db_session.add_all([member, session])
    await db_session.flush()

    record = AttendanceRecordFactory.create(
        session_id=session.id,
        member_id=member.id,
        status="PRESENT",
    )
    db_session.add(record)
    await db_session.commit()

    response = await attendance_client.get(f"/internal/attendance/member/{member.id}")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["member_id"] == str(member.id)
    assert data[0]["session_id"] == str(session.id)
    assert data[0]["status"] == "PRESENT"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_attendance_empty(attendance_client, db_session):
    """Returns empty list for member with no attendance."""
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await attendance_client.get(f"/internal/attendance/member/{member.id}")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_attendance_filtered_by_session_ids(
    attendance_client,
    db_session,
):
    """Filters attendance records by comma-separated session_ids."""
    member = MemberFactory.create()
    s1 = SessionFactory.create()
    s2 = SessionFactory.create()
    s3 = SessionFactory.create()
    db_session.add_all([member, s1, s2, s3])
    await db_session.flush()

    r1 = AttendanceRecordFactory.create(session_id=s1.id, member_id=member.id)
    r2 = AttendanceRecordFactory.create(session_id=s2.id, member_id=member.id)
    r3 = AttendanceRecordFactory.create(session_id=s3.id, member_id=member.id)
    db_session.add_all([r1, r2, r3])
    await db_session.commit()

    # Filter to only s1 and s2
    response = await attendance_client.get(
        f"/internal/attendance/member/{member.id}",
        params={"session_ids": f"{s1.id},{s2.id}"},
    )

    assert response.status_code == 200
    data = response.json()
    returned_session_ids = {item["session_id"] for item in data}
    assert str(s1.id) in returned_session_ids
    assert str(s2.id) in returned_session_ids
    assert str(s3.id) not in returned_session_ids


# ---------------------------------------------------------------------------
# GET /internal/attendance/session/{session_id}/member-ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_attendee_member_ids(attendance_client, db_session):
    """Returns distinct member IDs for session attendees."""
    m1 = MemberFactory.create()
    m2 = MemberFactory.create()
    session = SessionFactory.create()
    db_session.add_all([m1, m2, session])
    await db_session.flush()

    r1 = AttendanceRecordFactory.create(session_id=session.id, member_id=m1.id)
    r2 = AttendanceRecordFactory.create(session_id=session.id, member_id=m2.id)
    db_session.add_all([r1, r2])
    await db_session.commit()

    response = await attendance_client.get(
        f"/internal/attendance/session/{session.id}/member-ids"
    )

    assert response.status_code == 200
    data = response.json()
    assert str(m1.id) in data
    assert str(m2.id) in data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_attendee_member_ids_empty(attendance_client, db_session):
    """Returns empty list for session with no attendance."""
    session = SessionFactory.create()
    db_session.add(session)
    await db_session.commit()

    response = await attendance_client.get(
        f"/internal/attendance/session/{session.id}/member-ids"
    )

    assert response.status_code == 200
    assert response.json() == []
