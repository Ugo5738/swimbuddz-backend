import uuid
from datetime import datetime, time, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from services.sessions_service.routers import templates
from services.sessions_service.schemas.templates import (
    GenerateSessionsRequest,
    SessionTemplateUpdate,
)
from tests.conftest import make_admin_user


def template():
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="September swims",
        session_type="club",
        club_id=uuid.uuid4(),
        club_access_mode="paid_addon",
        is_active=True,
        auto_generate=True,
        description=None,
        pool_id=uuid.uuid4(),
        pod_id=None,
        location=None,
        location_name=None,
        pool_fee=520000,
        ride_share_fee=0,
        capacity=20,
        day_of_week=5,
        start_time=time(9),
        duration_minutes=90,
        ride_share_config=None,
        created_at=datetime.now(timezone.utc),
        updated_at=None,
    )


def database(row):
    return SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row)),
        add=Mock(),
        delete=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        rollback=AsyncMock(),
    )


async def test_archive_preserves_template_and_generated_sessions_and_restore_is_manual():
    row = template()
    db = database(row)
    result = await templates.update_template(
        row.id, SessionTemplateUpdate(is_active=False), db, make_admin_user()
    )
    assert result.is_active is False
    assert result.auto_generate is False
    db.delete.assert_not_awaited()
    assert db.execute.await_count == 1  # no session/booking mutation
    restored = await templates.update_template(
        row.id, SessionTemplateUpdate(is_active=True), db, make_admin_user()
    )
    assert restored.is_active is True
    assert restored.auto_generate is False


async def test_archived_template_cannot_generate_any_sessions():
    row = template()
    row.is_active = False
    db = database(row)
    with pytest.raises(HTTPException) as exc:
        await templates.generate_sessions(
            row.id, GenerateSessionsRequest(weeks=2), db, make_admin_user()
        )
    assert exc.value.status_code == 409
    db.add.assert_not_called()


async def test_old_delete_call_reports_archive_instead_of_internal_server_error():
    row = template()
    db = database(row)
    db.commit.side_effect = IntegrityError(
        "DELETE", {}, Exception("Referenced by sessions")
    )
    with pytest.raises(HTTPException) as exc:
        await templates.delete_template(row.id, db, make_admin_user())
    assert exc.value.status_code == 409
    assert "Archive" in exc.value.detail
    db.rollback.assert_awaited_once()


async def test_archive_missing_template_is_not_found():
    with pytest.raises(HTTPException) as exc:
        await templates.update_template(
            uuid.uuid4(),
            SessionTemplateUpdate(is_active=False),
            database(None),
            make_admin_user(),
        )
    assert exc.value.status_code == 404


async def test_unused_template_can_still_be_permanently_deleted():
    row = template()
    row.is_active = False
    db = database(row)
    await templates.delete_template(row.id, db, make_admin_user())
    db.delete.assert_awaited_once_with(row)
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()
