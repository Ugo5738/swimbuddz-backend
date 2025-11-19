import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
from datetime import datetime, timedelta

from services.attendance_service.router import get_current_member
from services.gateway_service.app.main import app

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
        registration_complete=True
    )

@pytest.mark.asyncio
async def test_sign_in_to_session(client: AsyncClient, db_session: AsyncSession):
    # 1. Create a session and member
    from services.sessions_service.models import Session, SessionLocation
    from services.members_service.models import Member
    
    # Create Member
    member = Member(
        id=MOCK_MEMBER_ID,
        auth_id="test-user-id",
        email="test@example.com",
        first_name="Test",
        last_name="Member",
        registration_complete=True
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
        pool_fee=500
    )
    db_session.add(session)
    await db_session.flush()
    
    # Verify session exists
    from sqlalchemy import select
    result = await db_session.execute(select(Session).where(Session.id == session_id))
    assert result.scalar_one_or_none() is not None, "Session not found in DB after flush"

    # 2. Override member dependency
    app.dependency_overrides[get_current_member] = mock_get_current_member

    # 3. Sign in
    payload = {
        "needs_ride": True,
        "can_offer_ride": False,
        "ride_notes": "Need pickup from downtown"
    }
    
    response = await client.post(f"/api/v1/sessions/{session_id}/sign-in", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == str(session_id)
    assert data["member_id"] == str(MOCK_MEMBER_ID)
    assert data["needs_ride"] is True
    assert data["payment_status"] == "pending"

    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_get_my_attendance_history(client: AsyncClient, db_session: AsyncSession):
    # 1. Create session, member and attendance
    from services.sessions_service.models import Session, SessionLocation
    from services.attendance_service.models import SessionAttendance, PaymentStatus
    from services.members_service.models import Member

    # Create Member
    member = Member(
        id=MOCK_MEMBER_ID,
        auth_id="test-user-id",
        email="test@example.com",
        first_name="Test",
        last_name="Member",
        registration_complete=True
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
        pool_fee=500
    )
    db_session.add(session)
    await db_session.flush() # Flush session and member first
    
    attendance = SessionAttendance(
        session_id=session_id,
        member_id=MOCK_MEMBER_ID,
        needs_ride=False,
        can_offer_ride=True,
        payment_status=PaymentStatus.PAID,
        total_fee=500
    )
    db_session.add(attendance)
    await db_session.flush()

    # 2. Override member dependency
    app.dependency_overrides[get_current_member] = mock_get_current_member

    # 3. Get history
    response = await client.get("/api/v1/me/attendance")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(a["session_id"] == str(session_id) for a in data)

    app.dependency_overrides.clear()
