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

    response = await sessions_client.get(
        f"/internal/sessions/cohorts/{cohort_id}/next-session"
    )
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

    response = await sessions_client.get(
        f"/internal/sessions/cohorts/{cohort_id}/session-ids"
    )
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert all(isinstance(item, str) for item in data)


# ---------------------------------------------------------------------------
# Error-path contracts (review finding FU7).
#
# Other services don't just depend on the *success* shape — they depend
# on the *error* shape. academy_service / attendance_service branch on
# 404 ("session/cohort gone") vs 5xx ("sessions_service down"), and the
# payments SESSION_BOOKING entitlement handler maps a 404 from the
# confirm endpoint to a 409 manual-refund (a 500 there would silently
# break the money path). These pin those error contracts so a refactor
# that turns a 404 into a 500/422 fails here first.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.contract
async def test_session_by_id_missing_is_404_contract(sessions_client):
    """Unknown session → clean 404, never 500. academy_service /
    attendance_service / payments distinguish 'gone' from 'service
    down' on this status code."""
    import uuid

    response = await sessions_client.get(f"/internal/sessions/{uuid.uuid4()}")
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
@pytest.mark.contract
async def test_next_session_none_is_404_contract(sessions_client):
    """No upcoming session for a cohort → 404 (not 200/null, not 500).
    academy cohort dashboards branch on this to show 'no session yet'."""
    import uuid

    response = await sessions_client.get(
        f"/internal/sessions/cohorts/{uuid.uuid4()}/next-session"
    )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
@pytest.mark.contract
async def test_session_ids_unknown_cohort_is_empty_not_404_contract(
    sessions_client,
):
    """Unknown cohort → 200 + []. academy_service iterates this list
    directly, so a 404 here would break cohort queries — the empty-list
    contract must hold even when the cohort has no sessions."""
    import uuid

    response = await sessions_client.get(
        f"/internal/sessions/cohorts/{uuid.uuid4()}/session-ids"
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


@pytest.mark.asyncio
@pytest.mark.contract
async def test_confirm_unknown_booking_is_404_contract(sessions_client):
    """POST confirm for an unknown booking → 404. The payments
    SESSION_BOOKING entitlement handler relies on exactly this: 404 →
    409 manual-refund. A 500/422 here would mis-handle a cleared
    payment (the money path)."""
    import uuid

    response = await sessions_client.post(
        f"/internal/sessions/bookings/{uuid.uuid4()}/confirm",
        json={},
    )
    assert response.status_code == 404, response.text


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
