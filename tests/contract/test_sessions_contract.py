"""Contract tests for sessions_service internal endpoints.

These tests verify that response shapes match what other services expect.
They are the "canary" — if a field is renamed or removed, these fail first.
"""

from datetime import datetime, timedelta, timezone

import pytest
from tests.factories import SessionFactory


@pytest.mark.asyncio
@pytest.mark.contract
async def test_session_by_id_contract(sessions_client, db_session):
    """Academy + attendance services depend on this shape."""
    session = SessionFactory.create()
    db_session.add(session)
    await db_session.commit()

    response = await sessions_client.get(f"/internal/sessions/{session.id}")
    assert response.status_code == 200
    data = response.json()

    required_fields = [
        "id",
        "title",
        "session_type",
        "status",
        "starts_at",
        "ends_at",
        "capacity",
    ]
    for field in required_fields:
        assert field in data, (
            f"Missing contract field '{field}' in session response. "
            f"Used by academy_service and attendance_service."
        )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_next_session_contract(sessions_client, db_session):
    """Academy service depends on next-session shape for cohort dashboards."""
    import uuid

    cohort_id = uuid.uuid4()
    session = SessionFactory.create(
        cohort_id=cohort_id,
        starts_at=datetime.now(timezone.utc) + timedelta(days=1),
        ends_at=datetime.now(timezone.utc) + timedelta(days=1, hours=2),
    )
    db_session.add(session)
    await db_session.commit()

    response = await sessions_client.get(f"/internal/cohorts/{cohort_id}/next-session")
    assert response.status_code == 200
    data = response.json()

    required_fields = ["starts_at", "title"]
    for field in required_fields:
        assert field in data, (
            f"Missing contract field '{field}' in next-session response. "
            f"Used by academy_service."
        )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_session_ids_for_cohort_contract(sessions_client, db_session):
    """Academy + attendance use session ID lists for cohort queries."""
    import uuid

    cohort_id = uuid.uuid4()
    session = SessionFactory.create(cohort_id=cohort_id)
    db_session.add(session)
    await db_session.commit()

    response = await sessions_client.get(f"/internal/cohorts/{cohort_id}/session-ids")
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert all(isinstance(item, str) for item in data)


@pytest.mark.asyncio
@pytest.mark.contract
async def test_scheduled_sessions_contract(sessions_client, db_session):
    """Communications service uses scheduled sessions for notifications."""
    session = SessionFactory.create(status="SCHEDULED")
    db_session.add(session)
    await db_session.commit()

    response = await sessions_client.get("/internal/sessions/scheduled")
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    if data:
        required_fields = ["id", "title", "starts_at", "ends_at", "session_type"]
        for field in required_fields:
            assert (
                field in data[0]
            ), f"Missing contract field '{field}' in scheduled sessions response."
