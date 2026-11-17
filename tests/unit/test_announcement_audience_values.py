"""Membership labels must never become invalid announcement enum bindings."""

import httpx
import pytest

from services.communications_service.routers.announcements import _helpers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "membership,expected",
    [
        ({"primary_tier": "prospect", "active_tiers": []}, {"community"}),
        ({"primary_tier": "unexpected", "active_tiers": ["prospect"]}, {"community"}),
        (
            {"primary_tier": "academy", "active_tiers": ["academy", "prospect"]},
            {"community", "club", "academy"},
        ),
        ({"primary_tier": "club", "active_tiers": ["club"]}, {"community", "club"}),
    ],
)
async def test_audience_filter_ignores_non_audience_membership_labels(
    monkeypatch, membership, expected
):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"membership": membership})
    )
    monkeypatch.setattr(
        _helpers.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=transport, **kwargs),
    )
    assert await _helpers._get_allowed_audiences("Bearer test") == expected
