"""Unit tests for the Supabase Admin invite helper (libs/common/supabase.py).

Guards the status mapping the finance-team invite flow relies on: a sent invite
-> "invited", an already-registered email -> "exists" (they just log in), and
anything else -> "error" (caller falls back to a manual Supabase invite). No
network: httpx is stubbed.
"""

import pytest

from libs.common import supabase as sb


class _Resp:
    def __init__(self, status_code: int, text: str = "{}"):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """Stands in for httpx.AsyncClient(...) used as an async context manager."""

    def __init__(self, resp: _Resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        return self._resp


def _patch(monkeypatch, resp: _Resp | None, *, configured: bool = True):
    class _S:
        SUPABASE_URL = "https://proj.supabase.co" if configured else ""
        SUPABASE_SERVICE_ROLE_KEY = "service-key" if configured else ""

    monkeypatch.setattr(sb, "get_settings", lambda: _S())
    if resp is not None:
        monkeypatch.setattr(sb.httpx, "AsyncClient", lambda **kw: _FakeClient(resp))


@pytest.mark.asyncio
async def test_invite_sent_returns_invited(monkeypatch):
    _patch(monkeypatch, _Resp(200))
    res = await sb.invite_user_by_email("accountant@example.com")
    assert res["status"] == "invited"


@pytest.mark.asyncio
async def test_already_registered_returns_exists(monkeypatch):
    _patch(
        monkeypatch, _Resp(422, '{"msg":"A user with this email already registered"}')
    )
    res = await sb.invite_user_by_email("existing@example.com")
    assert res["status"] == "exists"


@pytest.mark.asyncio
async def test_server_error_returns_error(monkeypatch):
    _patch(monkeypatch, _Resp(500, "boom"))
    res = await sb.invite_user_by_email("x@example.com")
    assert res["status"] == "error"


@pytest.mark.asyncio
async def test_unconfigured_returns_error_without_network(monkeypatch):
    # No SUPABASE_URL/key -> must return early, never touch httpx.
    _patch(monkeypatch, None, configured=False)

    def _boom(**kw):
        raise AssertionError("httpx must not be called when unconfigured")

    monkeypatch.setattr(sb.httpx, "AsyncClient", _boom)
    res = await sb.invite_user_by_email("x@example.com")
    assert res["status"] == "error"
