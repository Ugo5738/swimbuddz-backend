import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from libs.db.session import get_async_db
from services.attendance_service.app.main import app
from services.attendance_service.routers.member import get_current_member
from sqlalchemy.ext.asyncio import AsyncSession

# Mock member
MOCK_MEMBER_ID = uuid.uuid4()


async def mock_get_current_member():
    print("DEBUG: mock_get_current_member called")
    from services.members_service.models import Member

    return Member(
        id=MOCK_MEMBER_ID,
        auth_id="test-user-id",
        email="test@example.com",
        first_name="Test",
        last_name="Member",
        registration_complete=True,
    )


@pytest_asyncio.fixture
async def attendance_client(db_session: AsyncSession):
    async def _get_db():
        yield db_session

    app.dependency_overrides[get_async_db] = _get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_sign_in_to_session(
    attendance_client: AsyncClient, db_session: AsyncSession
):
    # 1. Create a session and member
    from services.members_service.models import Member
    from services.sessions_service.models import Session, SessionLocation

    # Create Member
    member = Member(
        id=MOCK_MEMBER_ID,
        auth_id="test-user-id",
        email="test@example.com",
        first_name="Test",
        last_name="Member",
        registration_complete=True,
    )
    db_session.add(member)

    session_id = uuid.uuid4()
    session = Session(
        id=session_id,
        title="Attendance Test Session",
        description="Testing sign-in",
        location=SessionLocation.MAIN_POOL,
        start_time=datetime.utcnow() + timedelta(hours=1),
        end_time=datetime.utcnow() + timedelta(hours=2),
        capacity=10,
        pool_fee=500,
    )
    db_session.add(session)
    await db_session.flush()

    # Verify session exists
    from sqlalchemy import select

    result = await db_session.execute(select(Session).where(Session.id == session_id))
    assert (
        result.scalar_one_or_none() is not None
    ), "Session not found in DB after flush"

    # 2. Override member dependency
    app.dependency_overrides[get_current_member] = mock_get_current_member

    # 3. Sign in
    from services.attendance_service.models.enums import (
        AttendanceRole,
        AttendanceStatus,
    )

    payload = {
        "status": AttendanceStatus.PRESENT.value,
        "role": AttendanceRole.SWIMMER.value,
        "notes": "Ready to swim",
    }

    response = await attendance_client.post(
        f"/attendance/sessions/{session_id}/sign-in", json=payload
    )
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == str(session_id)
    assert data["member_id"] == str(MOCK_MEMBER_ID)
    assert data["status"] == AttendanceStatus.PRESENT.value
    assert data["role"] == AttendanceRole.SWIMMER.value

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_my_attendance_history(
    attendance_client: AsyncClient, db_session: AsyncSession
):
    # 1. Create session, member and attendance
    from services.attendance_service.models import AttendanceRecord
    from services.members_service.models import Member
    from services.sessions_service.models import Session, SessionLocation

    # Create Member
    member = Member(
        id=MOCK_MEMBER_ID,
        auth_id="test-user-id",
        email="test@example.com",
        first_name="Test",
        last_name="Member",
        registration_complete=True,
    )
    db_session.add(member)

    session_id = uuid.uuid4()
    session = Session(
        id=session_id,
        title="History Test Session",
        description="Testing history",
        location=SessionLocation.OPEN_WATER,
        start_time=datetime.utcnow() - timedelta(days=1),
        end_time=datetime.utcnow() - timedelta(days=1, hours=1),
        capacity=10,
        pool_fee=500,
    )
    db_session.add(session)
    await db_session.flush()  # Flush session and member first

    attendance = AttendanceRecord(
        session_id=session_id,
        member_id=MOCK_MEMBER_ID,
        status="present",
        role="swimmer",
    )
    db_session.add(attendance)
    await db_session.flush()

    # 2. Override member dependency
    app.dependency_overrides[get_current_member] = mock_get_current_member

    # 3. Get history
    response = await attendance_client.get("/attendance/me")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(a["session_id"] == str(session_id) for a in data)

    app.dependency_overrides.clear()
    assert any(a["session_id"] == str(session_id) for a in data)

    app.dependency_overrides.clear()
