"""Guest subrouters reach Sessions and preserve private capability headers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from services.gateway_service.app import main as gateway


@pytest.mark.asyncio
async def test_guest_gateway_routes_and_cors(monkeypatch):
    response = httpx.Response(200, json={"ok": True})
    downstream = SimpleNamespace(
        get=AsyncMock(return_value=response),
        post=AsyncMock(return_value=response),
        delete=AsyncMock(return_value=response),
    )
    monkeypatch.setattr(gateway.clients, "sessions_client", downstream)
    monkeypatch.setattr(gateway.limiter, "enabled", False)
    app = gateway.create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        paths = [
            ("GET", "/sessions/session/guest-pass", "get"),
            ("POST", "/sessions/session/guest-passes", "post"),
            ("POST", "/sessions/session/guest-link-events", "post"),
            ("GET", "/sessions/session/guest-share-link", "get"),
            ("GET", "/guest-passes/pass", "get"),
            ("POST", "/guest-passes/pass/checkout", "post"),
            ("GET", "/admin/guest-passes/funnel", "get"),
            ("POST", "/admin/guest-passes/pass/payment-link", "post"),
            ("POST", "/admin/sessions/session/guest-booking-links", "post"),
            ("GET", "/admin/sessions/session/roster", "get"),
            ("DELETE", "/admin/guest-booking-links/grant", "delete"),
        ]
        for method, path, handler in paths:
            result = await client.request(
                method,
                f"/api/v1{path}",
                headers={
                    "X-Guest-Invite-Token": "scoped-invite",
                    "X-Guest-Pass-Token": "private-receipt",
                },
            )
            assert result.status_code == 200, result.text
            call = getattr(downstream, handler).await_args
            assert call.args[0] == path
            assert call.kwargs["headers"]["x-guest-invite-token"] == "scoped-invite"
            assert call.kwargs["headers"]["x-guest-pass-token"] == "private-receipt"
        cors = await client.options(
            "/api/v1/sessions/session/guest-pass",
            headers={
                "Origin": "https://swimbuddz.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Guest-Invite-Token,X-Guest-Booking-Token,X-Guest-Pass-Token",
            },
        )
        assert cors.status_code == 200
