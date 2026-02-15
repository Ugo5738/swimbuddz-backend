"""Integration tests for sessions_service internal endpoints."""

from datetime import datetime, timedelta, timezone

import pytest
from tests.factories import MemberFactory, SessionCoachFactory, SessionFactory

# ---------------------------------------------------------------------------
# GET /internal/sessions/{session_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_by_id(sessions_client, db_session):
    """Internal session lookup returns correct session data."""
    session = SessionFactory.create()
    db_session.add(session)
    await db_session.commit()

    response = await sessions_client.get(f"/internal/sessions/{session.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(session.id)
    assert data["title"] == session.title
    assert data["session_type"] == "club"
    assert data["status"] == "scheduled"
    assert data["capacity"] == 20


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_by_id_not_found(sessions_client):
    """Returns 404 for non-existent session."""
    import uuid

    response = await sessions_client.get(f"/internal/sessions/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /internal/cohorts/{cohort_id}/next-session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_next_session_for_cohort(sessions_client, db_session):
    """Returns the next upcoming SCHEDULED session for a cohort."""
    import uuid

    cohort_id = uuid.uuid4()

    # Past session (should be skipped)
    past = SessionFactory.create(
        cohort_id=cohort_id,
        starts_at=datetime.now(timezone.utc) - timedelta(days=1),
        ends_at=datetime.now(timezone.utc) - timedelta(hours=22),
    )
    # Next upcoming session
    upcoming = SessionFactory.create(
        cohort_id=cohort_id,
        title="Next Lesson",
        starts_at=datetime.now(timezone.utc) + timedelta(days=1),
        ends_at=datetime.now(timezone.utc) + timedelta(days=1, hours=2),
    )
    # Further future session
    future = SessionFactory.create(
        cohort_id=cohort_id,
        starts_at=datetime.now(timezone.utc) + timedelta(days=7),
        ends_at=datetime.now(timezone.utc) + timedelta(days=7, hours=2),
    )
    db_session.add_all([past, upcoming, future])
    await db_session.commit()

    response = await sessions_client.get(f"/internal/cohorts/{cohort_id}/next-session")

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Next Lesson"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_next_session_for_cohort_not_found(sessions_client):
    """Returns 404 when no upcoming sessions exist for the cohort."""
    import uuid

    response = await sessions_client.get(
        f"/internal/cohorts/{uuid.uuid4()}/next-session"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /internal/cohorts/{cohort_id}/session-ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_ids_for_cohort(sessions_client, db_session):
    """Returns all session IDs for a given cohort."""
    import uuid

    cohort_id = uuid.uuid4()

    s1 = SessionFactory.create(cohort_id=cohort_id)
    s2 = SessionFactory.create(cohort_id=cohort_id)
    # Different cohort — should not appear
    s3 = SessionFactory.create(cohort_id=uuid.uuid4())
    db_session.add_all([s1, s2, s3])
    await db_session.commit()

    response = await sessions_client.get(f"/internal/cohorts/{cohort_id}/session-ids")

    assert response.status_code == 200
    data = response.json()
    assert str(s1.id) in data
    assert str(s2.id) in data
    assert str(s3.id) not in data


# ---------------------------------------------------------------------------
# GET /internal/cohorts/{cohort_id}/completed-session-ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_completed_session_ids_for_cohort(sessions_client, db_session):
    """Returns only completed session IDs for a cohort."""
    import uuid

    cohort_id = uuid.uuid4()

    completed = SessionFactory.create(cohort_id=cohort_id, status="COMPLETED")
    scheduled = SessionFactory.create(cohort_id=cohort_id, status="SCHEDULED")
    db_session.add_all([completed, scheduled])
    await db_session.commit()

    response = await sessions_client.get(
        f"/internal/cohorts/{cohort_id}/completed-session-ids"
    )

    assert response.status_code == 200
    data = response.json()
    assert str(completed.id) in data
    assert str(scheduled.id) not in data


# ---------------------------------------------------------------------------
# GET /internal/sessions/{session_id}/coaches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_coach_ids(sessions_client, db_session):
    """Returns coach member IDs assigned to a session."""
    session = SessionFactory.create()
    db_session.add(session)
    await db_session.flush()

    member = MemberFactory.create()
    db_session.add(member)
    await db_session.flush()

    coach_assignment = SessionCoachFactory.create(
        session_id=session.id,
        coach_id=member.id,
    )
    db_session.add(coach_assignment)
    await db_session.commit()

    response = await sessions_client.get(f"/internal/sessions/{session.id}/coaches")

    assert response.status_code == 200
    data = response.json()
    assert str(member.id) in data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_session_coach_ids_empty(sessions_client, db_session):
    """Returns empty list when no coaches assigned."""
    session = SessionFactory.create()
    db_session.add(session)
    await db_session.commit()

    response = await sessions_client.get(f"/internal/sessions/{session.id}/coaches")

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# GET /internal/sessions/scheduled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_scheduled_sessions(sessions_client, db_session):
    """Returns only SCHEDULED sessions."""
    scheduled = SessionFactory.create(status="SCHEDULED", title="Active")
    cancelled = SessionFactory.create(status="CANCELLED", title="Cancelled")
    db_session.add_all([scheduled, cancelled])
    await db_session.commit()

    response = await sessions_client.get("/internal/sessions/scheduled")

    assert response.status_code == 200
    data = response.json()
    titles = [s["title"] for s in data]
    assert "Active" in titles
    assert "Cancelled" not in titles
