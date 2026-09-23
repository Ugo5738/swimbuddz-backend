"""Event Session reads must not bypass their parent Event privacy policy."""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.sessions_service.routers.member import (
    _decorate_session_for_user,
    _decorate_sessions_for_user,
)
from tests.factories import SessionFactory


@pytest.mark.asyncio
async def test_anonymous_session_list_omits_event_sessions():
    event_session = SessionFactory.create(event_id=uuid.uuid4())
    community_session = SessionFactory.create(session_type="community")

    visible = await _decorate_sessions_for_user(
        [event_session, community_session],
        None,
        AsyncMock(),
    )

    assert visible == [community_session]


@pytest.mark.asyncio
async def test_anonymous_event_session_detail_is_not_found():
    event_session = SessionFactory.create(event_id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc_info:
        await _decorate_session_for_user(event_session, None, AsyncMock())

    assert exc_info.value.status_code == 404
