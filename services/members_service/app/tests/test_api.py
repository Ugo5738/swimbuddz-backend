import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from services.gateway_service.app.main import app

# Mock user for authenticated tests
MOCK_USER_ID = "test-user-id"
MOCK_EMAIL = "test@example.com"


async def mock_get_current_user():
    return AuthUser(sub=MOCK_USER_ID, email=MOCK_EMAIL, role="authenticated")


@pytest.mark.asyncio
async def test_create_pending_registration(
    client: AsyncClient, db_session: AsyncSession
):
    payload = {
        "email": "new@example.com",
        "first_name": "New",
        "last_name": "User",
        "phone": "1234567890",
    }
    response = await client.post("/api/v1/pending-registrations/", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == payload["email"]


@pytest.mark.asyncio
async def test_complete_pending_registration(
    client: AsyncClient, db_session: AsyncSession
):
    # 1. Create pending registration first
    payload = {"email": MOCK_EMAIL, "first_name": "Test", "last_name": "Member"}
    await client.post("/api/v1/pending-registrations/", json=payload)

    # 2. Override auth dependency to simulate logged in user matching the pending email
    app.dependency_overrides[get_current_user] = mock_get_current_user

    # 3. Complete registration
    response = await client.post("/api/v1/pending-registrations/complete")
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == MOCK_EMAIL
    assert data["first_name"] == "Test"
    assert data["registration_complete"] is True

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_current_member_profile(
    client: AsyncClient, db_session: AsyncSession
):
    # 1. Create a member directly (or via flow)
    from services.members_service.models import Member

    member = Member(
        auth_id=MOCK_USER_ID,
        email=MOCK_EMAIL,
        first_name="Existing",
        last_name="Member",
        registration_complete=True,
    )
    db_session.add(member)
    await db_session.commit()

    # 2. Authenticate
    app.dependency_overrides[get_current_user] = mock_get_current_user

    # 3. Get profile
    response = await client.get("/api/v1/members/me")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == MOCK_EMAIL
    assert data["first_name"] == "Existing"

    app.dependency_overrides.clear()
