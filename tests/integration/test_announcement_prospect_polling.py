"""The actual unread SQL must accept an unpaid prospect's audience filters."""

from uuid import uuid4

import httpx
import pytest
from starlette.requests import Request

from services.communications_service.models import (
    AnnouncementAudience,
    AnnouncementStatus,
)
from services.communications_service.routers.announcements import _helpers, crud
from tests.factories import AnnouncementFactory


@pytest.mark.asyncio
@pytest.mark.integration
async def test_prospect_unread_poll_returns_community_count_without_enum_error(
    db_session, monkeypatch
):
    community = AnnouncementFactory.create(
        audience=AnnouncementAudience.COMMUNITY, status=AnnouncementStatus.PUBLISHED
    )
    club = AnnouncementFactory.create(
        audience=AnnouncementAudience.CLUB, status=AnnouncementStatus.PUBLISHED
    )
    db_session.add_all([community, club])
    await db_session.commit()
    original = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"membership": {"primary_tier": "prospect", "active_tiers": []}}
        )
    )
    monkeypatch.setattr(
        _helpers.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=transport, **kwargs),
    )
    request = Request({"type": "http", "headers": [(b"authorization", b"Bearer test")]})
    response = await crud.get_unread_count(request, uuid4(), db_session)
    assert response["unread_count"] == 1
