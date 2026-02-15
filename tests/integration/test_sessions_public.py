"""Integration tests for sessions_service PUBLIC API endpoints.

Tests the user-facing endpoints: list sessions, get session, create session,
update session, delete session, session stats, and cancel.

NOTE: The DB session_status_enum uses UPPERCASE names (SCHEDULED, IN_PROGRESS,
COMPLETED, CANCELLED). The DRAFT status exists in the Python model but was not
added to the DB enum via migration. Tests that need DRAFT use the API's create
endpoint instead of direct factory insertion.
"""

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from tests.factories import SessionFactory

# ---------------------------------------------------------------------------
# GET /sessions/ — List sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_sessions(sessions_client, db_session):
    """Returns list of sessions."""
    s1 = SessionFactory.create()  # defaults to SCHEDULED
    s2 = SessionFactory.create()
    db_session.add_all([s1, s2])
    await db_session.commit()

    response = await sessions_client.get("/sessions/")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 2


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_sessions_with_type_filter(sessions_client, db_session):
    """Filters sessions by type when types param is provided."""
    club = SessionFactory.create(session_type="CLUB")
    community = SessionFactory.create(session_type="COMMUNITY")
    db_session.add_all([club, community])
    await db_session.commit()

    response = await sessions_client.get("/sessions/?types=club")

    assert response.status_code == 200
    data = response.json()
    # All returned sessions should be club type
    for item in data:
        assert item["session_type"].lower() == "club"


# ---------------------------------------------------------------------------
# GET /sessions/stats — Session statistics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_stats(sessions_client, db_session):
    """Returns upcoming session count."""
    s = SessionFactory.create()
    db_session.add(s)
    await db_session.commit()

    response = await sessions_client.get("/sessions/stats")

    assert response.status_code == 200
    data = response.json()
    assert "upcoming_sessions_count" in data
    assert data["upcoming_sessions_count"] >= 1


# ---------------------------------------------------------------------------
# GET /sessions/{id} — Get session detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_by_id(sessions_client, db_session):
    """Fetch a specific session by ID."""
    s = SessionFactory.create()
    db_session.add(s)
    await db_session.commit()

    response = await sessions_client.get(f"/sessions/{s.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(s.id)
    assert data["title"] == s.title


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_not_found(sessions_client, db_session):
    """Returns 404 for non-existent session."""
    fake_id = str(uuid.uuid4())
    response = await sessions_client.get(f"/sessions/{fake_id}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /sessions/ — Create session (admin only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_session(sessions_client, db_session):
    """Admin can create a new session via the API."""
    from tests.factories import _tomorrow

    tomorrow = _tomorrow()
    payload = {
        "title": "New Club Session",
        "session_type": "club",
        "starts_at": tomorrow.isoformat(),
        "ends_at": (tomorrow + timedelta(hours=2)).isoformat(),
        "timezone": "Africa/Lagos",
        "location": "sunfit_pool",
        "capacity": 20,
        "pool_fee": 2000.0,
    }

    response = await sessions_client.post("/sessions/", json=payload)

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["title"] == "New Club Session"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_session_missing_required_fields(sessions_client, db_session):
    """Returns 422 for incomplete session data."""
    payload = {"title": "Incomplete"}

    response = await sessions_client.post("/sessions/", json=payload)

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /sessions/{id} — Update session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_session(sessions_client, db_session):
    """Admin can update session fields."""
    s = SessionFactory.create()
    db_session.add(s)
    await db_session.commit()

    response = await sessions_client.patch(
        f"/sessions/{s.id}",
        json={"title": "Updated Title"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_session_not_found(sessions_client, db_session):
    """Returns 404 for non-existent session."""
    fake_id = str(uuid.uuid4())
    response = await sessions_client.patch(
        f"/sessions/{fake_id}",
        json={"title": "Nope"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /sessions/{id} — Delete session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_session(sessions_client, db_session):
    """Admin can delete a session."""
    s = SessionFactory.create()
    db_session.add(s)
    await db_session.commit()

    response = await sessions_client.delete(f"/sessions/{s.id}")

    assert response.status_code == 204


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_session_not_found(sessions_client, db_session):
    """Returns 404 for non-existent session."""
    fake_id = str(uuid.uuid4())
    response = await sessions_client.delete(f"/sessions/{fake_id}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /sessions/{id}/cancel — Cancel session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cancel_session(sessions_client, db_session):
    """Admin can cancel a scheduled session."""
    s = SessionFactory.create()  # defaults to SCHEDULED
    db_session.add(s)
    await db_session.commit()

    with patch(
        "services.communications_service.tasks.cancel_session_notifications",
        new_callable=AsyncMock,
    ):
        response = await sessions_client.post(f"/sessions/{s.id}/cancel")

    assert response.status_code == 200
    data = response.json()
    assert data["status"].lower() == "cancelled"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cancel_already_cancelled_session(sessions_client, db_session):
    """Returns 400 when session is already cancelled."""
    s = SessionFactory.create(status="CANCELLED")
    db_session.add(s)
    await db_session.commit()

    response = await sessions_client.post(f"/sessions/{s.id}/cancel")

    assert response.status_code == 400
    assert "already cancelled" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cancel_completed_session(sessions_client, db_session):
    """Returns 400 when trying to cancel a completed session."""
    s = SessionFactory.create(status="COMPLETED")
    db_session.add(s)
    await db_session.commit()

    response = await sessions_client.post(f"/sessions/{s.id}/cancel")

    assert response.status_code == 400
    assert "completed" in response.json()["detail"].lower()
