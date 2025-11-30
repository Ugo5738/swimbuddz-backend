"""Test session deletion to identify the actual error."""

import pytest
from httpx import AsyncClient
from datetime import datetime, timedelta


@pytest.mark.asyncio
async def test_delete_session(client: AsyncClient, db_session):
    """Test deleting a session."""
    # First, create a session
    from services.sessions_service.models import Session

    session = Session(
        title="Test Session",
        location="main_pool",
        start_time=datetime.utcnow() + timedelta(days=1),
        end_time=datetime.utcnow() + timedelta(days=1, hours=2),
        pool_fee=2000,
        capacity=20,
    )

    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    session_id = str(session.id)

    # Mock admin auth
    from libs.auth.dependencies import require_admin
    from libs.auth.models import AuthUser

    async def mock_admin():
        return AuthUser(id="test-admin-id", email="admin@test.com", role="admin")

    from services.gateway_service.app.main import app

    app.dependency_overrides[require_admin] = mock_admin

    # Now try to delete the session
    print(f"Attempting to delete session: {session_id}")

    response = await client.delete(f"/api/v1/sessions/{session_id}")

    print(f"Response status: {response.status_code}")
    print(f"Response text: {response.text}")

    # Check the response
    assert (
        response.status_code == 204
    ), f"Expected 204, got {response.status_code}: {response.text}"

    # Verify the session is deleted
    from sqlalchemy import select

    query = select(Session).where(Session.id == session.id)
    result = await db_session.execute(query)
    deleted_session = result.scalar_one_or_none()

    assert deleted_session is None, "Session should be deleted"
