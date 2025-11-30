import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from services.gateway_service.app.main import app

# Mock admin user
MOCK_ADMIN_ID = "admin-user-id"
MOCK_ADMIN_EMAIL = "admin@example.com"


async def mock_require_admin():
    return AuthUser(sub=MOCK_ADMIN_ID, email=MOCK_ADMIN_EMAIL, role="service_role")


@pytest.mark.asyncio
async def test_create_session_admin(client: AsyncClient, db_session: AsyncSession):
    # 1. Override admin dependency
    app.dependency_overrides[require_admin] = mock_require_admin

    # 2. Create session
    start_time = datetime.utcnow() + timedelta(days=1)
    end_time = start_time + timedelta(hours=1)

    payload = {
        "title": "Morning Swim",
        "description": "Laps and drills",
        "location": "main_pool",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "capacity": 20,
        "pool_fee": 1500,
    }

    response = await client.post("/api/v1/sessions/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Morning Swim"
    assert data["pool_fee"] == 1500

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_sessions(client: AsyncClient, db_session: AsyncSession):
    # 1. Create a session directly
    from services.sessions_service.models import Session, SessionLocation

    session = Session(
        title="Evening Swim",
        description="Relaxed pace",
        location=SessionLocation.MAIN_POOL,  # Assuming enum exists or string
        start_time=datetime.utcnow() + timedelta(days=2),
        end_time=datetime.utcnow() + timedelta(days=2, hours=1),
        capacity=15,
        pool_fee=1200,
    )
    db_session.add(session)
    await db_session.commit()

    # 2. List sessions (public)
    response = await client.get("/api/v1/sessions/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert any(s["title"] == "Evening Swim" for s in data)


@pytest.mark.asyncio
async def test_get_session_details(client: AsyncClient, db_session: AsyncSession):
    # 1. Create a session
    from services.sessions_service.models import Session, SessionLocation
    import uuid

    session_id = uuid.uuid4()
    session = Session(
        id=session_id,
        title="Specific Session",
        description="Details here",
        location=SessionLocation.DIVING_POOL,
        start_time=datetime.utcnow() + timedelta(days=3),
        end_time=datetime.utcnow() + timedelta(days=3, hours=1),
        capacity=10,
        pool_fee=1000,
    )
    db_session.add(session)
    await db_session.commit()

    # 2. Get details
    response = await client.get(f"/api/v1/sessions/{session_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Specific Session"
    assert data["id"] == str(session_id)
