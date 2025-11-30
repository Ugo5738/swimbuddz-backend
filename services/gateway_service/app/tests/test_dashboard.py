import pytest
import uuid
from datetime import datetime, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from services.gateway_service.app.main import app
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser

# Mock data constants
MOCK_USER_ID = "test-user-id"
MOCK_ADMIN_ID = "test-admin-id"
MOCK_MEMBER_ID = uuid.uuid4()


async def mock_get_current_user():
    return AuthUser(sub=MOCK_USER_ID, email="test@example.com", role="authenticated")


async def mock_require_admin():
    return AuthUser(sub=MOCK_ADMIN_ID, email="admin@example.com", role="admin")


@pytest.mark.asyncio
async def test_get_member_dashboard(client: AsyncClient, db_session: AsyncSession):
    # 1. Setup Data
    from services.members_service.models import Member
    from services.sessions_service.models import Session, SessionLocation
    from services.attendance_service.models import AttendanceRecord
    from services.communications_service.models import (
        Announcement,
        AnnouncementCategory,
    )

    # Create Member
    member = Member(
        id=MOCK_MEMBER_ID,
        auth_id=MOCK_USER_ID,
        email="test@example.com",
        first_name="Dashboard",
        last_name="User",
        registration_complete=True,
    )
    db_session.add(member)

    # Create Session
    session = Session(
        id=uuid.uuid4(),
        title="Dashboard Session",
        description="Testing dashboard",
        location=SessionLocation.MAIN_POOL,
        start_time=datetime.utcnow() + timedelta(days=1),
        end_time=datetime.utcnow() + timedelta(days=1, hours=1),
        capacity=10,
        pool_fee=500,
    )
    db_session.add(session)
    await db_session.flush()  # Flush session and member first

    # Create Attendance
    attendance = AttendanceRecord(
        session_id=session.id,
        member_id=member.id,
        status="PRESENT",
        role="SWIMMER",
        notes="Ready",
    )
    db_session.add(attendance)

    # Create Announcement
    announcement = Announcement(
        title="Dashboard News",
        body="Welcome to the dashboard",
        category=AnnouncementCategory.GENERAL,
        published_at=datetime.utcnow() - timedelta(hours=1),
    )
    db_session.add(announcement)

    await db_session.flush()

    # 2. Override dependency
    app.dependency_overrides[get_current_user] = mock_get_current_user

    # 3. Call Endpoint
    response = await client.get("/api/v1/me/dashboard")
    assert response.status_code == 200
    data = response.json()

    # 4. Verify Aggregation
    assert data["member"]["email"] == "test@example.com"
    assert len(data["upcoming_sessions"]) == 1
    assert data["upcoming_sessions"][0]["title"] == "Dashboard Session"
    assert len(data["recent_attendance"]) == 1
    assert len(data["latest_announcements"]) == 1
    assert data["latest_announcements"][0]["title"] == "Dashboard News"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_admin_dashboard_stats(client: AsyncClient, db_session: AsyncSession):
    # 1. Setup Data (reuse or add more)
    from services.members_service.models import Member

    # Ensure at least one member exists (from previous test or new)
    # Since tests share DB session in this setup (function scoped but same DB),
    # we might need to be careful. But here we just add one more.
    member = Member(
        id=uuid.uuid4(),
        auth_id="another-user",
        email="another@example.com",
        first_name="Another",
        last_name="User",
        registration_complete=True,
    )
    db_session.add(member)
    await db_session.flush()

    # 2. Override dependency
    app.dependency_overrides[require_admin] = mock_require_admin

    # 3. Call Endpoint
    response = await client.get("/api/v1/admin/dashboard-stats")
    assert response.status_code == 200
    data = response.json()

    # 4. Verify Stats
    assert data["total_members"] >= 1
    assert data["active_members"] >= 1
    # We created one session in the previous test (or this one if run independently)
    # so upcoming_sessions_count should be >= 1

    app.dependency_overrides.clear()
