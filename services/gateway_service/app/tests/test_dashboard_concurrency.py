"""Dependency barriers fail if independent dashboard reads become sequential."""

import asyncio

import httpx
import pytest
from starlette.requests import Request

from services.gateway_service.app.routers import dashboard


@pytest.mark.asyncio
async def test_admin_fetches_all_stats_concurrently_and_degrades(monkeypatch):
    started = []
    ready = asyncio.Event()

    class Client:
        def __init__(self, label, payload=None, fail=False):
            self.label, self.payload, self.fail = label, payload, fail

        async def get(self, path, headers=None):
            assert headers == {"Authorization": "Bearer member-token"}
            started.append(self.label)
            if len(started) == 3:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=1)
            if self.fail:
                raise httpx.ConnectError("offline")
            return httpx.Response(200, json=self.payload)

    monkeypatch.setattr(
        dashboard.clients, "members_client", Client("members", {"total_members": 17})
    )
    monkeypatch.setattr(
        dashboard.clients, "sessions_client", Client("sessions", fail=True)
    )
    monkeypatch.setattr(
        dashboard.clients,
        "communications_client",
        Client("announcements", {"recent_announcements_count": 9}),
    )
    request = Request(
        {"type": "http", "headers": [(b"authorization", b"Bearer member-token")]}
    )
    result = await dashboard.get_admin_dashboard_stats(request, current_user=None)
    assert result.total_members == 17
    assert result.recent_announcements_count == 9
    assert result.upcoming_sessions_count == 0
    assert result.errors == {"sessions": "Sessions service unavailable"}


@pytest.mark.asyncio
async def test_member_checks_required_profile_before_parallel_optional_sections(
    monkeypatch,
):
    profile_loaded = False
    started = []
    ready = asyncio.Event()

    class RequiredClient:
        async def get(self, path, headers=None):
            nonlocal profile_loaded
            profile_loaded = True
            return httpx.Response(200, json={"first_name": "Amara"})

    class OptionalClient:
        async def get(self, path, headers=None):
            assert profile_loaded
            started.append(path)
            if len(started) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=1)
            return httpx.Response(200, json=[])

    monkeypatch.setattr(dashboard.clients, "members_client", RequiredClient())
    monkeypatch.setattr(dashboard.clients, "sessions_client", OptionalClient())
    monkeypatch.setattr(dashboard.clients, "communications_client", OptionalClient())
    result = await dashboard.get_member_dashboard(
        Request({"type": "http", "headers": []}), current_user=None
    )
    assert result.member == {"first_name": "Amara"}
    assert result.errors == {}


@pytest.mark.asyncio
async def test_missing_profile_prevents_optional_fetches(monkeypatch):
    class MissingClient:
        async def get(self, path, headers=None):
            response = httpx.Response(
                404,
                json={"detail": "missing"},
                request=httpx.Request("GET", "http://test"),
            )
            response.raise_for_status()

    class UnexpectedClient:
        async def get(self, *args, **kwargs):
            pytest.fail("Optional data must not load without a member profile")

    monkeypatch.setattr(dashboard.clients, "members_client", MissingClient())
    monkeypatch.setattr(dashboard.clients, "sessions_client", UnexpectedClient())
    monkeypatch.setattr(dashboard.clients, "communications_client", UnexpectedClient())
    with pytest.raises(dashboard.HTTPException) as error:
        await dashboard.get_member_dashboard(
            Request({"type": "http", "headers": []}), current_user=None
        )
    assert error.value.status_code == 404
