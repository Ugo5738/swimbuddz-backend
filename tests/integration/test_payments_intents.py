"""Integration + unit tests for payments_service/routers/intents.py.

These tests pin the public + helper surface of `intents.py` BEFORE we split
the 2,333-line module so the split can be verified with a green test suite
(per docs/CONVENTIONS.md §12.3).

Coverage strategy:
  - Pure helpers (`_verify_paystack_signature`, `_to_kobo`) tested as
    unit functions; no DB, no httpx, no fixtures needed.
  - Route handlers exercised through the existing `payments_client`
    (httpx ASGI transport against the live payments FastAPI app) with
    deterministic auth + DB overrides from `_wire_app`.
  - External I/O (Paystack HTTPS, members-service entitlement POSTs,
    notification dispatch) is mocked at the module-attribute boundary so
    the routes exercise their own logic without leaking to the network.

What we deliberately DON'T attempt here:
  - `create_payment_intent` happy path — its branching has 5+ purposes
    and each calls a different downstream service. That belongs in a
    follow-up suite once the split exposes one module per purpose.
  - Paystack webhook signature flow — lives in `webhooks.py`, separate
    file; intents.py only contributes `_verify_paystack_signature` and
    `_mark_paid_and_apply` (both covered here).
"""

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: unit tests (no fixtures required)
# ---------------------------------------------------------------------------


def test_verify_paystack_signature_accepts_valid_hmac(monkeypatch):
    """Valid HMAC-SHA512 of body with the configured secret must verify."""
    from services.payments_service.routers import intents

    secret = "sk_test_FAKE_KEY_123"
    monkeypatch.setattr(intents.settings, "PAYSTACK_SECRET_KEY", secret)

    body = b'{"event":"charge.success","data":{"reference":"PAY-XYZ"}}'
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha512
    ).hexdigest()

    assert intents._verify_paystack_signature(body, expected) is True


def test_verify_paystack_signature_rejects_tampered_body(monkeypatch):
    """If the body changes after signing, signature must NOT verify."""
    from services.payments_service.routers import intents

    secret = "sk_test_FAKE_KEY_123"
    monkeypatch.setattr(intents.settings, "PAYSTACK_SECRET_KEY", secret)

    original = b'{"event":"charge.success","data":{"reference":"PAY-XYZ"}}'
    tampered = b'{"event":"charge.success","data":{"reference":"PAY-EVIL"}}'
    sig = hmac.new(secret.encode("utf-8"), original, hashlib.sha512).hexdigest()

    assert intents._verify_paystack_signature(tampered, sig) is False


def test_verify_paystack_signature_rejects_wrong_signature(monkeypatch):
    """Garbage signature must fail constant-time compare."""
    from services.payments_service.routers import intents

    monkeypatch.setattr(intents.settings, "PAYSTACK_SECRET_KEY", "sk_test_X")

    assert intents._verify_paystack_signature(b'{"a":1}', "deadbeef") is False
    assert intents._verify_paystack_signature(b'{"a":1}', "") is False


def test_to_kobo_basic_naira_amounts():
    """₦1.00 → 100 kobo; ₦20,000 → 2,000,000 kobo."""
    from services.payments_service.routers.intents import _to_kobo

    assert _to_kobo(1.00) == 100
    assert _to_kobo(20_000) == 2_000_000
    assert _to_kobo(0) == 0


def test_to_kobo_rounds_half_up():
    """Sub-kobo fractions must use ROUND_HALF_UP so we never under-charge."""
    from services.payments_service.routers.intents import _to_kobo

    # 0.005 NGN → 0.01 NGN (round up to 1 kobo)
    assert _to_kobo(0.005) == 1
    # 0.014 NGN → 0.01 NGN (round down) → 1 kobo
    assert _to_kobo(0.014) == 1
    # 0.015 NGN → 0.02 NGN (round up) → 2 kobo
    assert _to_kobo(0.015) == 2


# ---------------------------------------------------------------------------
# Route fixtures — re-override admin user so payment.member_auth_id matches
# ---------------------------------------------------------------------------


def _override_current_user_with(payments_app, auth_id: str):
    """Pin get_current_user / require_admin to a known auth_id.

    The default `_wire_app` admin is created with a fresh uuid each test;
    routes that filter by `member_auth_id == current_user.user_id`
    (e.g. /me, /paystack/verify/{ref}) need a stable id we can write into
    a Payment row beforehand.
    """
    from libs.auth.dependencies import (
        get_current_user,
        get_optional_user,
        require_admin,
    )

    from tests.conftest import make_admin_user

    admin = make_admin_user(user_id=auth_id, email="admin@test.com")

    async def _get_admin():
        return admin

    payments_app.dependency_overrides[get_current_user] = _get_admin
    payments_app.dependency_overrides[get_optional_user] = _get_admin
    payments_app.dependency_overrides[require_admin] = _get_admin


# ---------------------------------------------------------------------------
# GET /payments/me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_my_payments_filters_to_current_user(
    payments_client, db_session
):
    """/me returns only payments owned by current_user.user_id."""
    from services.payments_service.app.main import app as payments_app
    from tests.factories import PaymentFactory

    my_auth_id = str(uuid.uuid4())
    other_auth_id = str(uuid.uuid4())
    _override_current_user_with(payments_app, my_auth_id)

    mine = PaymentFactory.create(member_auth_id=my_auth_id)
    theirs = PaymentFactory.create(member_auth_id=other_auth_id)
    db_session.add_all([mine, theirs])
    await db_session.commit()

    response = await payments_client.get("/payments/me")
    assert response.status_code == 200
    refs = {row["reference"] for row in response.json()}
    assert mine.reference in refs
    assert theirs.reference not in refs


# ---------------------------------------------------------------------------
# DELETE /payments/admin/members/by-auth/{auth_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_delete_member_payments_removes_rows(
    payments_client, db_session
):
    from sqlalchemy import select

    from services.payments_service.models import Payment
    from tests.factories import PaymentFactory

    target_auth_id = str(uuid.uuid4())
    p1 = PaymentFactory.create(member_auth_id=target_auth_id)
    p2 = PaymentFactory.create(member_auth_id=target_auth_id)
    bystander = PaymentFactory.create(member_auth_id=str(uuid.uuid4()))
    db_session.add_all([p1, p2, bystander])
    await db_session.commit()

    response = await payments_client.delete(
        f"/payments/admin/members/by-auth/{target_auth_id}"
    )
    assert response.status_code == 200
    assert response.json() == {"deleted": 2}

    # Verify the bystander survives
    remaining = (
        await db_session.execute(
            select(Payment).where(Payment.id == bystander.id)
        )
    ).scalar_one_or_none()
    assert remaining is not None


# ---------------------------------------------------------------------------
# POST /payments/{reference}/complete  (admin-only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_complete_payment_marks_paid_and_applies_entitlement(
    payments_client, db_session
):
    """Happy path: PENDING → PAID, entitlement applied, paid_at populated."""
    from services.payments_service.models import PaymentStatus
    from tests.factories import PaymentFactory

    payment = PaymentFactory.create(status="pending")
    db_session.add(payment)
    await db_session.commit()

    # Mock the entitlement application — we only care that the endpoint
    # calls it, not the cross-service mechanics (covered elsewhere).
    with patch(
        "services.payments_service.routers.intents._apply_entitlement_with_tracking",
        AsyncMock(return_value=None),
    ) as mock_apply:
        response = await payments_client.post(
            f"/payments/{payment.reference}/complete",
            json={
                "provider": "paystack",
                "provider_reference": "PSK_REF_001",
                "note": "manual admin completion",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == PaymentStatus.PAID.value
    assert body["provider"] == "paystack"
    assert body["provider_reference"] == "PSK_REF_001"
    assert body["paid_at"] is not None
    assert (body.get("payment_metadata") or {}).get("admin_note") == (
        "manual admin completion"
    )
    mock_apply.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_complete_payment_is_idempotent_when_already_paid(
    payments_client, db_session
):
    """If the payment is already PAID, return it as-is — no re-apply."""
    from tests.factories import PaymentFactory

    payment = PaymentFactory.create(
        status="paid",
        provider="paystack",
        provider_reference="PSK_OLD",
        paid_at=datetime.now(timezone.utc),
    )
    db_session.add(payment)
    await db_session.commit()

    with patch(
        "services.payments_service.routers.intents._apply_entitlement_with_tracking",
        AsyncMock(return_value=None),
    ) as mock_apply:
        response = await payments_client.post(
            f"/payments/{payment.reference}/complete",
            json={"provider": "paystack", "provider_reference": "PSK_NEW"},
        )

    assert response.status_code == 200
    body = response.json()
    # Provider/provider_reference should NOT be overwritten on the idempotent
    # short-circuit path.
    assert body["provider_reference"] == "PSK_OLD"
    mock_apply.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_complete_payment_rejects_duplicate_provider_reference(
    payments_client, db_session
):
    """409 when another PENDING payment already claims this provider_reference."""
    from tests.factories import PaymentFactory

    existing = PaymentFactory.create(
        status="paid", provider="paystack", provider_reference="PSK_DUPE"
    )
    target = PaymentFactory.create(status="pending")
    db_session.add_all([existing, target])
    await db_session.commit()

    response = await payments_client.post(
        f"/payments/{target.reference}/complete",
        json={"provider": "paystack", "provider_reference": "PSK_DUPE"},
    )

    assert response.status_code == 409, response.text
    assert "provider_reference" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_complete_payment_returns_404_for_unknown_reference(
    payments_client,
):
    response = await payments_client.post(
        "/payments/PAY-DOES-NOT-EXIST/complete",
        json={"provider": "paystack"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Payment not found"


# ---------------------------------------------------------------------------
# POST /payments/paystack/verify/{reference}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_verify_paystack_short_circuits_when_already_paid_and_applied(
    payments_client, db_session
):
    """Already-paid + entitlement-applied payment is returned as-is.

    This is the cheap idempotency path: no Paystack HTTP call, no entitlement
    re-application. Critical for cost + correctness on the client-driven
    fallback verification endpoint.
    """
    from services.payments_service.app.main import app as payments_app
    from tests.factories import PaymentFactory

    my_auth_id = str(uuid.uuid4())
    _override_current_user_with(payments_app, my_auth_id)

    payment = PaymentFactory.create(
        member_auth_id=my_auth_id,
        status="paid",
        provider="paystack",
        provider_reference=f"PSK-{uuid.uuid4().hex[:8]}",
        paid_at=datetime.now(timezone.utc),
        entitlement_applied_at=datetime.now(timezone.utc),
    )
    db_session.add(payment)
    await db_session.commit()

    with patch(
        "services.payments_service.routers.intents._verify_paystack_transaction",
        AsyncMock(side_effect=AssertionError("must NOT be called")),
    ), patch(
        "services.payments_service.routers.intents._apply_entitlement_with_tracking",
        AsyncMock(side_effect=AssertionError("must NOT be called")),
    ):
        response = await payments_client.post(
            f"/payments/paystack/verify/{payment.reference}"
        )

    assert response.status_code == 200, response.text
    assert response.json()["reference"] == payment.reference


@pytest.mark.asyncio
@pytest.mark.integration
async def test_verify_paystack_returns_404_for_unknown_or_other_users_payment(
    payments_client, db_session
):
    """A user must not be able to verify a payment they don't own."""
    from services.payments_service.app.main import app as payments_app
    from tests.factories import PaymentFactory

    my_auth_id = str(uuid.uuid4())
    other_auth_id = str(uuid.uuid4())
    _override_current_user_with(payments_app, my_auth_id)

    not_mine = PaymentFactory.create(
        member_auth_id=other_auth_id, status="pending"
    )
    db_session.add(not_mine)
    await db_session.commit()

    response = await payments_client.post(
        f"/payments/paystack/verify/{not_mine.reference}"
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Payment not found"


# ---------------------------------------------------------------------------
# POST /payments/admin/{reference}/replay-entitlement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_replay_entitlement_rejects_non_paid_payments(
    payments_client, db_session
):
    """Cannot replay entitlement on a payment that was never paid.

    Pass the PaymentStatus enum directly (not the string) so the in-memory
    SQLAlchemy identity-map instance shared with the endpoint matches what
    a fresh production load would yield. The route reads `status.value`.
    """
    from services.payments_service.models import PaymentStatus
    from tests.factories import PaymentFactory

    pending = PaymentFactory.create(status=PaymentStatus.PENDING)
    db_session.add(pending)
    await db_session.commit()

    response = await payments_client.post(
        f"/payments/admin/{pending.reference}/replay-entitlement"
    )

    assert response.status_code == 400
    assert "not paid" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_replay_entitlement_happy_path_invokes_apply(
    payments_client, db_session
):
    """Replay endpoint calls _apply_entitlement_with_tracking on PAID rows."""
    from tests.factories import PaymentFactory

    paid = PaymentFactory.create(
        status="paid",
        provider="paystack",
        provider_reference="PSK_OK",
        paid_at=datetime.now(timezone.utc),
        entitlement_error="prior_failure",
    )
    db_session.add(paid)
    await db_session.commit()

    with patch(
        "services.payments_service.routers.intents._apply_entitlement_with_tracking",
        AsyncMock(return_value=None),
    ) as mock_apply:
        response = await payments_client.post(
            f"/payments/admin/{paid.reference}/replay-entitlement"
        )

    assert response.status_code == 200, response.text
    mock_apply.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_replay_entitlement_returns_404_for_unknown_reference(
    payments_client,
):
    response = await payments_client.post(
        "/payments/admin/PAY-NOPE/replay-entitlement"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /payments/pricing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_pricing_returns_membership_fee_config(payments_client):
    """Pricing endpoint exposes the public membership fee schedule."""
    response = await payments_client.get("/payments/pricing")
    assert response.status_code == 200
    data = response.json()
    assert data["currency"] == "NGN"
    for field in (
        "community_annual",
        "club_quarterly",
        "club_biannual",
        "club_annual",
    ):
        assert field in data
        assert isinstance(data[field], (int, float))
