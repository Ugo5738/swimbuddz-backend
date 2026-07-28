import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.attendance_service.app.main import app as attendance_app
from services.attendance_service.routers.member._shared import get_current_member
from tests.factories import AttendanceRecordFactory


@pytest.mark.asyncio
@pytest.mark.integration
async def test_attendance_history_batches_session_enrichment_once(
    attendance_client, db_session, monkeypatch
):
    member_id = uuid.uuid4()
    first_session_id = uuid.uuid4()
    second_session_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    records = [
        AttendanceRecordFactory.create(
            member_id=member_id,
            session_id=first_session_id,
            created_at=now,
        ),
        AttendanceRecordFactory.create(
            member_id=member_id,
            session_id=first_session_id,
            created_at=now - timedelta(minutes=1),
        ),
        AttendanceRecordFactory.create(
            member_id=member_id,
            session_id=second_session_id,
            created_at=now - timedelta(minutes=2),
        ),
        AttendanceRecordFactory.create(
            member_id=member_id,
            session_id=second_session_id,
            created_at=now - timedelta(minutes=3),
        ),
    ]
    db_session.add_all(records)
    await db_session.commit()

    async def current_member_override():
        return SimpleNamespace(id=member_id)

    attendance_app.dependency_overrides[get_current_member] = current_member_override
    session_batch = AsyncMock(
        return_value=[
            {
                "id": str(first_session_id),
                "title": "First session",
                "session_type": "club",
                "starts_at": now.isoformat(),
                "location_name": "Sunfit Pool",
            },
            {
                "id": str(second_session_id),
                "title": "Second session",
                "session_type": "community",
                "starts_at": now.isoformat(),
                "location_name": "Rowe Park",
            },
        ]
    )
    monkeypatch.setattr(
        "services.attendance_service.routers.member.lists.get_sessions_by_ids",
        session_batch,
    )

    response = await attendance_client.get("/attendance/me?limit=3")

    assert response.status_code == 200, response.text
    assert len(response.json()) == 3
    session_batch.assert_awaited_once()
    requested_ids = session_batch.await_args.args[0]
    assert requested_ids == [str(first_session_id), str(second_session_id)]
    assert response.headers["x-result-count"] == "3"
    assert response.headers["x-has-more"] == "true"
    assert response.headers["x-next-offset"] == "3"
    assert "session_batch" in response.headers["server-timing"]

    session_batch.reset_mock()
    unexpanded = await attendance_client.get(
        "/attendance/me?include_session=false&limit=2"
    )
    assert unexpanded.status_code == 200
    session_batch.assert_not_awaited()
    assert all(item["session"] is None for item in unexpanded.json())
