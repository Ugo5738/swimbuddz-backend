"""Integration tests for registration flow.

These tests use the database fixture and test the full registration flow
through the API endpoints.
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_complete_pending_registration_creates_member(client: AsyncClient, db_session):
    """Test that completing registration creates a member record."""
    from services.members_service.models import Member, PendingRegistration
    from libs.auth.dependencies import get_current_user
    from libs.auth.models import AuthUser
    from services.gateway_service.app.main import app

    # Setup: Create pending registration
    pending = PendingRegistration(
        email="test@example.com",
        profile_data_json='{"first_name": "Test", "last_name": "User", "email": "test@example.com"}',
    )
    db_session.add(pending)
    await db_session.commit()

    # Mock authenticated user (simulating post-email-confirmation state)
    async def mock_user():
        return AuthUser(user_id="auth-123", email="test@example.com", role="member")

    app.dependency_overrides[get_current_user] = mock_user

    try:
        response = await client.post("/api/v1/pending-registrations/complete")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data["email"] == "test@example.com"
        assert data["first_name"] == "Test"
        assert data["last_name"] == "User"
        assert data["approval_status"] == "approved"
        
        # Verify pending was deleted
        from sqlalchemy import select
        result = await db_session.execute(
            select(PendingRegistration).where(PendingRegistration.email == "test@example.com")
        )
        assert result.scalar_one_or_none() is None, "Pending registration should be deleted"
        
        # Verify member was created
        result = await db_session.execute(
            select(Member).where(Member.email == "test@example.com")
        )
        member = result.scalar_one_or_none()
        assert member is not None, "Member should be created"
        assert member.auth_id == "auth-123"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_complete_registration_is_idempotent(client: AsyncClient, db_session):
    """Test that completing registration twice returns existing member."""
    from services.members_service.models import Member
    from libs.auth.dependencies import get_current_user
    from libs.auth.models import AuthUser
    from services.gateway_service.app.main import app

    # Setup: Create existing member (simulating already-completed registration)
    member = Member(
        auth_id="auth-456",
        email="existing@example.com",
        first_name="Existing",
        last_name="User",
        registration_complete=True,
        approval_status="approved",
    )
    db_session.add(member)
    await db_session.commit()

    async def mock_user():
        return AuthUser(user_id="auth-456", email="existing@example.com", role="member")

    app.dependency_overrides[get_current_user] = mock_user

    try:
        response = await client.post("/api/v1/pending-registrations/complete")
        
        # Should succeed and return existing member (idempotent)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data["email"] == "existing@example.com"
        assert data["first_name"] == "Existing"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_complete_registration_without_pending_returns_404(client: AsyncClient, db_session):
    """Test that completing without pending registration returns 404."""
    from libs.auth.dependencies import get_current_user
    from libs.auth.models import AuthUser
    from services.gateway_service.app.main import app

    # Mock a user who has no pending registration
    async def mock_user():
        return AuthUser(user_id="auth-no-pending", email="nopending@example.com", role="member")

    app.dependency_overrides[get_current_user] = mock_user

    try:
        response = await client.post("/api/v1/pending-registrations/complete")
        
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_current_member_profile(client: AsyncClient, db_session):
    """Test GET /api/v1/members/me returns current member profile."""
    from services.members_service.models import Member
    from libs.auth.dependencies import get_current_user
    from libs.auth.models import AuthUser
    from services.gateway_service.app.main import app

    # Setup: Create member
    member = Member(
        auth_id="auth-me-test",
        email="me@example.com",
        first_name="Me",
        last_name="Test",
        registration_complete=True,
        approval_status="approved",
        membership_tier="community",
        membership_tiers=["community"],
    )
    db_session.add(member)
    await db_session.commit()

    async def mock_user():
        return AuthUser(user_id="auth-me-test", email="me@example.com", role="member")

    app.dependency_overrides[get_current_user] = mock_user

    try:
        response = await client.get("/api/v1/members/me")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        assert data["email"] == "me@example.com"
        assert data["first_name"] == "Me"
        assert data["membership_tier"] == "community"
    finally:
        app.dependency_overrides.clear()
