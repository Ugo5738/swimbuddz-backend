"""Integration tests for the Gumroad money-in path of the public analyzer.

Webhook auth (path token + seller_id), the MANDATORY license re-verify before
granting, idempotency on sale_id, refund→revoke, plus the license-redeem and
the coarse credits endpoint. ``verify_license`` and the GUMROAD_* config are
patched where they're USED (services.ai_service.routers.public.*).
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from services.ai_service.services import credit_ops

WEBHOOK = "/ai/public/gumroad/webhook"
REDEEM = "/ai/public/credits/redeem"
CREDITS = "/ai/public/credits"

_PUB = "services.ai_service.routers.public"


@pytest.fixture
def gumroad_env():
    with (
        patch(f"{_PUB}.GUMROAD_PING_TOKEN", "test-ping-token"),
        patch(f"{_PUB}.GUMROAD_SELLER_ID", "seller-123"),
    ):
        yield


def _verify(**purchase):
    return patch(f"{_PUB}.verify_license", new=AsyncMock(return_value=purchase or None))


def _sale_form(
    email, *, permalink="puxlbz", sale_id=None, license_key="LIC-1", **extra
):
    f = {
        "seller_id": "seller-123",
        "sale_id": sale_id or f"sale-{uuid.uuid4().hex}",
        "email": email,
        "product_permalink": permalink,
        "license_key": license_key,
    }
    f.update(extra)
    return f


def _email() -> str:
    return f"gr-{uuid.uuid4().hex}@example.com"


# ── webhook auth ─────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_bad_token_403(ai_client, gumroad_env):
    r = await ai_client.post(f"{WEBHOOK}?token=wrong", data=_sale_form(_email()))
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_missing_token_403(ai_client, gumroad_env):
    r = await ai_client.post(WEBHOOK, data=_sale_form(_email()))
    assert r.status_code == 403, r.text


# ── webhook grant / verify / idempotency ─────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_grants_on_verified_sale_idempotent(
    ai_client, db_session, gumroad_env
):
    email = _email()
    sale = f"sale-{uuid.uuid4().hex}"
    form = _sale_form(email, permalink="puxlbz", sale_id=sale)
    with _verify(sale_id=sale, email=email):
        r = await ai_client.post(f"{WEBHOOK}?token=test-ping-token", data=form)
    assert r.status_code == 200, r.text
    bal = await credit_ops.get_balance(db_session, raw_email=email)
    assert bal["remaining_credits"] == 10  # puxlbz

    # Replay the SAME sale → idempotent, no double grant.
    with _verify(sale_id=sale, email=email):
        r2 = await ai_client.post(f"{WEBHOOK}?token=test-ping-token", data=form)
    assert r2.status_code == 200
    bal2 = await credit_ops.get_balance(db_session, raw_email=email)
    assert bal2["remaining_credits"] == 10  # still 10


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_no_license_never_grants(ai_client, db_session, gumroad_env):
    email = _email()
    form = _sale_form(email, license_key="")  # no verifiable key
    r = await ai_client.post(f"{WEBHOOK}?token=test-ping-token", data=form)
    assert r.status_code == 200  # acked
    bal = await credit_ops.get_balance(db_session, raw_email=email)
    assert bal["remaining_credits"] == 0  # not granted


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_verify_sale_mismatch_no_grant(
    ai_client, db_session, gumroad_env
):
    email = _email()
    sale = f"sale-{uuid.uuid4().hex}"
    form = _sale_form(email, sale_id=sale)
    with _verify(sale_id="some-other-sale"):  # verified sale != posted sale
        r = await ai_client.post(f"{WEBHOOK}?token=test-ping-token", data=form)
    assert r.status_code == 200
    bal = await credit_ops.get_balance(db_session, raw_email=email)
    assert bal["remaining_credits"] == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_seller_mismatch_drops(ai_client, db_session, gumroad_env):
    email = _email()
    form = _sale_form(email, seller_id="not-us")
    with _verify(sale_id="x"):
        r = await ai_client.post(f"{WEBHOOK}?token=test-ping-token", data=form)
    assert r.status_code == 200  # 200-and-drop
    bal = await credit_ops.get_balance(db_session, raw_email=email)
    assert bal["remaining_credits"] == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_refund_revokes_and_flags(ai_client, db_session, gumroad_env):
    email = _email()
    sale = f"sale-{uuid.uuid4().hex}"
    with _verify(sale_id=sale):
        await ai_client.post(
            f"{WEBHOOK}?token=test-ping-token",
            data=_sale_form(email, permalink="vrjec", sale_id=sale),
        )
    assert (await credit_ops.get_balance(db_session, raw_email=email))[
        "remaining_credits"
    ] == 1

    r = await ai_client.post(
        f"{WEBHOOK}?token=test-ping-token",
        data=_sale_form(email, permalink="vrjec", sale_id=sale, refunded="true"),
    )
    assert r.status_code == 200
    bal = await credit_ops.get_balance(db_session, raw_email=email)
    assert bal["remaining_credits"] == 0
    assert bal["can_submit_free"] is False  # flagged out of the free tier


# ── redeem ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_redeem_grants_then_409_on_replay(ai_client):
    email = _email()
    sale = f"sale-{uuid.uuid4().hex}"
    body = {"email": email, "license_key": "LIC-XYZ", "product_permalink": "fgopu"}
    with _verify(sale_id=sale):
        r = await ai_client.post(REDEEM, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["granted"] == 3  # fgopu
    assert r.json()["remaining_credits"] == 3

    with _verify(sale_id=sale):
        r2 = await ai_client.post(REDEEM, json=body)
    assert r2.status_code == 409, r2.text
    assert r2.json()["detail"]["reason"] == "already_redeemed"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_redeem_invalid_license_422(ai_client):
    body = {"email": _email(), "license_key": "bad", "product_permalink": "vrjec"}
    with _verify():  # verify_license → None
        r = await ai_client.post(REDEEM, json=body)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_redeem_unknown_product_400(ai_client):
    body = {"email": _email(), "license_key": "k", "product_permalink": "nope"}
    r = await ai_client.post(REDEEM, json=body)
    assert r.status_code == 400, r.text


# ── credits balance (coarse) ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_credits_coarse_no_free_used(ai_client):
    email = _email()
    r = await ai_client.get(f"{CREDITS}?email={email}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["can_submit_free"] is True  # fresh email
    assert body["remaining_credits"] == 0
    assert "free_used" not in body  # the enumeration field is never exposed


# ── verify_license rejects reversed sales (security-review regression) ──


class _FakeResp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *a, **k):
        return _FakeResp(self._payload)


@pytest.mark.asyncio
async def test_verify_license_rejects_reversed_sale():
    from services.ai_service.services import gumroad

    refunded = {"success": True, "purchase": {"sale_id": "s1", "refunded": True}}
    with patch(
        "services.ai_service.services.gumroad.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(refunded),
    ):
        assert await gumroad.verify_license("vrjec", "key") is None

    ok = {"success": True, "purchase": {"sale_id": "s1"}}
    with patch(
        "services.ai_service.services.gumroad.httpx.AsyncClient",
        lambda *a, **k: _FakeClient(ok),
    ):
        got = await gumroad.verify_license("vrjec", "key")
        assert got and got["sale_id"] == "s1"
