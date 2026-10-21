"""Regression tests: never replay non-idempotent gateway requests."""

from unittest.mock import AsyncMock

import httpx
import pytest

from services.gateway_service.app import clients


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_gateway_does_not_retry_mutations_after_transport_failure(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    mock_client = AsyncMock()
    mock_client.request.side_effect = httpx.ReadTimeout("downstream response lost")
    monkeypatch.setattr(clients, "_get_shared_client", lambda: mock_client)
    gateway = clients.ServiceClient("http://downstream")

    with pytest.raises(httpx.ReadTimeout):
        await gateway._request(method, "/sensitive-operation")

    assert mock_client.request.await_count == 1


@pytest.mark.asyncio
async def test_gateway_still_retries_safe_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_client = AsyncMock()
    request = httpx.Request("GET", "http://downstream/members")
    success = httpx.Response(200, json={"ok": True}, request=request)
    mock_client.request.side_effect = [
        httpx.ConnectError("temporary connection failure"),
        success,
    ]
    monkeypatch.setattr(clients, "_get_shared_client", lambda: mock_client)
    monkeypatch.setattr(clients.asyncio, "sleep", AsyncMock())
    gateway = clients.ServiceClient("http://downstream")

    result = await gateway.get("/members")

    assert result.status_code == 200
    assert mock_client.request.await_count == 2
