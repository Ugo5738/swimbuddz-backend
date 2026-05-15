"""Integration tests for the Paystack webhook handler.

The webhook is the production money-path: Paystack calls us with a signed
JSON payload when a transaction lands a terminal state. Every other path
(member-driven /paystack/verify/{ref}, admin /complete) is a *fallback*.
If the webhook breaks, payments still complete but entitlement
application gets delayed; if signature verification breaks, the endpoint
becomes spoofable. Both are tested here.

Scope:
  - signature verification (positive + missing + bad)
  - charge.success → marks PAID + triggers entitlement
  - charge.success amount mismatch → records error, does NOT pay
  - charge.failed → marks FAILED (and triggers academy notification for
    ACADEMY_COHORT specifically)
  - idempotency: replay of charge.success on already-paid + applied row
    short-circuits (no re-entitlement)
  - unknown reference returns 200 (Paystack should not retry)
  - wallet topup fallback (no Payment row but metadata.type == wallet_topup)
"""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


def _sign(body: bytes, secret: str) -> str:
    """Compute the x-paystack-signature header value."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha512).hexdigest()


def _set_secret(monkeypatch, secret: str = "sk_test_FAKE_KEY"):
    """Pin PAYSTACK_SECRET_KEY on both the webhook module AND the helper
    module where `_verify_paystack_signature` reads it.
    """
    from services.payments_service.routers import webhooks
    from services.payments_service.routers.intents import _paystack

    monkeypatch.setattr(_paystack.settings, "PAYSTACK_SECRET_KEY", secret)
    monkeypatch.setattr(webhooks.settings, "PAYSTACK_SECRET_KEY", secret)


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_rejects_missing_signature(payments_client, monkeypatch):
    """No signature header → 401, no DB changes."""
    _set_secret(monkeypatch)

    body = json.dumps(
        {"event": "charge.success", "data": {"reference": "PAY-NONE"}}
    )
    response = await payments_client.post(
        "/payments/webhooks/paystack", content=body
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid signature"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_rejects_wrong_signature(payments_client, monkeypatch):
    """Forged signature → 401."""
    _set_secret(monkeypatch)

    body = json.dumps(
        {"event": "charge.success", "data": {"reference": "PAY-NONE"}}
    )
    response = await payments_client.post(
        "/payments/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": "deadbeef"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_rejects_tampered_body(payments_client, monkeypatch):
    """Body modified after signing → 401."""
    _set_secret(monkeypatch, secret="sk_test_TAMPERED")

    original_body = json.dumps(
        {"event": "charge.success", "data": {"reference": "PAY-ORIG"}}
    ).encode("utf-8")
    sig = _sign(original_body, "sk_test_TAMPERED")

    tampered_body = json.dumps(
        {"event": "charge.success", "data": {"reference": "PAY-EVIL"}}
    )
    response = await payments_client.post(
        "/payments/webhooks/paystack",
        content=tampered_body,
        headers={"x-paystack-signature": sig},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Happy path: charge.success → PAID + entitlement triggered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_charge_success_marks_paid_and_triggers_entitlement(
    payments_client, db_session, monkeypatch
):
    """charge.success with matching amount → status flips to PAID and
    `_apply_entitlement_with_tracking` is invoked via `_mark_paid_and_apply`.

    We mock the entitlement call (covered separately) and assert on the
    DB row + the mock activation.
    """
    from sqlalchemy import select

    from services.payments_service.models import Payment, PaymentStatus
    from tests.factories import PaymentFactory

    secret = "sk_test_HAPPY"
    _set_secret(monkeypatch, secret=secret)

    payment = PaymentFactory.create(
        status=PaymentStatus.PENDING, amount=20000.0  # ₦20,000 → 2,000,000 kobo
    )
    db_session.add(payment)
    await db_session.commit()

    payload = {
        "event": "charge.success",
        "data": {
            "reference": payment.reference,
            "amount": 2_000_000,  # matches _to_kobo(20000)
            "paid_at": "2026-05-15T10:00:00Z",
        },
    }
    body = json.dumps(payload).encode("utf-8")

    # _mark_paid_and_apply lives in intents/_entitlement/_dispatcher.py and
    # is imported into webhooks.py at module load. Patch the bound name
    # on the webhooks module so we don't need to actually drive entitlement.
    async def _stub_mark_paid(db, payment, provider, provider_reference,
                              paid_at, provider_payload=None):
        payment.status = PaymentStatus.PAID
        payment.provider = provider
        payment.provider_reference = provider_reference
        payment.paid_at = paid_at
        db.add(payment)
        await db.commit()
        return payment

    with patch(
        "services.payments_service.routers.webhooks._mark_paid_and_apply",
        AsyncMock(side_effect=_stub_mark_paid),
    ) as mock_mark:
        response = await payments_client.post(
            "/payments/webhooks/paystack",
            content=body,
            headers={"x-paystack-signature": _sign(body, secret)},
        )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    mock_mark.assert_awaited_once()

    # Reload to confirm DB write
    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.PAID
    assert payment.provider == "paystack"
    assert payment.provider_reference == payment.reference


# ---------------------------------------------------------------------------
# Amount mismatch — payment must NOT be marked paid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_charge_success_amount_mismatch_blocks_entitlement(
    payments_client, db_session, monkeypatch
):
    """If Paystack-reported amount doesn't match our Payment.amount,
    we record an `entitlement_error` and leave the payment in PENDING.

    This is the guardrail against tampered or stale Paystack payloads.
    """
    from services.payments_service.models import PaymentStatus
    from tests.factories import PaymentFactory

    secret = "sk_test_MISMATCH"
    _set_secret(monkeypatch, secret=secret)

    payment = PaymentFactory.create(
        status=PaymentStatus.PENDING, amount=20000.0  # expects 2,000,000 kobo
    )
    db_session.add(payment)
    await db_session.commit()

    payload = {
        "event": "charge.success",
        "data": {
            "reference": payment.reference,
            "amount": 100,  # ₦1 — way less than expected
            "paid_at": "2026-05-15T10:00:00Z",
        },
    }
    body = json.dumps(payload).encode("utf-8")

    with patch(
        "services.payments_service.routers.webhooks._mark_paid_and_apply",
        AsyncMock(side_effect=AssertionError("must NOT be called")),
    ):
        response = await payments_client.post(
            "/payments/webhooks/paystack",
            content=body,
            headers={"x-paystack-signature": _sign(body, secret)},
        )

    assert response.status_code == 200
    await db_session.refresh(payment)
    # The payment is still PENDING — we did NOT auto-pay on a mismatch
    assert payment.status == PaymentStatus.PENDING
    assert payment.entitlement_error is not None
    assert "amount mismatch" in payment.entitlement_error.lower()


# ---------------------------------------------------------------------------
# charge.failed → marks FAILED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_charge_failed_marks_payment_failed(
    payments_client, db_session, monkeypatch
):
    from services.payments_service.models import PaymentStatus
    from tests.factories import PaymentFactory

    secret = "sk_test_FAILED"
    _set_secret(monkeypatch, secret=secret)

    payment = PaymentFactory.create(status=PaymentStatus.PENDING)
    db_session.add(payment)
    await db_session.commit()

    payload = {
        "event": "charge.failed",
        "data": {"reference": payment.reference, "reason": "Insufficient funds"},
    }
    body = json.dumps(payload).encode("utf-8")

    response = await payments_client.post(
        "/payments/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": _sign(body, secret)},
    )

    assert response.status_code == 200
    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.FAILED
    assert payment.provider == "paystack"


# ---------------------------------------------------------------------------
# Idempotency: replay on already-PAID + entitlement-applied row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_idempotent_when_already_paid_and_applied(
    payments_client, db_session, monkeypatch
):
    """If a payment is already PAID *and* entitlement_applied_at is set,
    a duplicate charge.success webhook is a no-op (no re-apply, no DB write).

    Paystack retries on 5xx and is famous for occasional double-fires;
    this idempotency guard protects against double-grant of entitlements.
    """
    from services.payments_service.models import PaymentStatus
    from tests.factories import PaymentFactory

    secret = "sk_test_IDEMP"
    _set_secret(monkeypatch, secret=secret)

    paid_at = datetime.now(timezone.utc)
    payment = PaymentFactory.create(
        status=PaymentStatus.PAID,
        amount=20000.0,
        provider="paystack",
        provider_reference="PSK_ORIG",
        paid_at=paid_at,
        entitlement_applied_at=paid_at,
    )
    db_session.add(payment)
    await db_session.commit()

    payload = {
        "event": "charge.success",
        "data": {
            "reference": payment.reference,
            "amount": 2_000_000,
            "paid_at": "2026-05-15T10:00:00Z",
        },
    }
    body = json.dumps(payload).encode("utf-8")

    with patch(
        "services.payments_service.routers.webhooks._mark_paid_and_apply",
        AsyncMock(side_effect=AssertionError("must NOT be called on duplicate")),
    ):
        response = await payments_client.post(
            "/payments/webhooks/paystack",
            content=body,
            headers={"x-paystack-signature": _sign(body, secret)},
        )

    assert response.status_code == 200
    await db_session.refresh(payment)
    # Provider reference is unchanged — the duplicate webhook didn't
    # overwrite it.
    assert payment.provider_reference == "PSK_ORIG"


# ---------------------------------------------------------------------------
# Unknown reference — Paystack must get 200 to stop retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_unknown_reference_returns_200_with_received(
    payments_client, monkeypatch
):
    """An unknown reference (not in our DB and not a wallet topup) must
    return 200 — otherwise Paystack will keep retrying forever on a
    payment we can't reconcile.
    """
    secret = "sk_test_UNKNOWN"
    _set_secret(monkeypatch, secret=secret)

    payload = {
        "event": "charge.success",
        "data": {
            "reference": f"PAY-UNKNOWN-{uuid.uuid4().hex[:6]}",
            "amount": 100,
        },
    }
    body = json.dumps(payload).encode("utf-8")

    response = await payments_client.post(
        "/payments/webhooks/paystack",
        content=body,
        headers={"x-paystack-signature": _sign(body, secret)},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}


# ---------------------------------------------------------------------------
# Wallet topup fallback — no Payment row, but metadata.type == wallet_topup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_webhook_wallet_topup_dispatches_to_wallet_service(
    payments_client, monkeypatch
):
    """When a reference isn't in our Payment table but the Paystack metadata
    flags it as a wallet topup, we forward the result to wallet_service.
    The wallet service owns the topup row lifecycle; we just relay.
    """
    secret = "sk_test_WALLET"
    _set_secret(monkeypatch, secret=secret)

    topup_ref = f"WLT-TOP-{uuid.uuid4().hex[:8]}"
    payload = {
        "event": "charge.success",
        "data": {
            "reference": topup_ref,
            "amount": 50_000,  # ₦500
            "metadata": {"type": "wallet_topup"},
        },
    }
    body = json.dumps(payload).encode("utf-8")

    # Mock the wallet service call; assert we forward the right fields
    from httpx import Response

    fake_response = Response(
        status_code=200,
        json={"topup_id": str(uuid.uuid4()), "status": "completed"},
    )
    with patch(
        "services.payments_service.routers.webhooks.internal_post",
        AsyncMock(return_value=fake_response),
    ) as mock_post:
        response = await payments_client.post(
            "/payments/webhooks/paystack",
            content=body,
            headers={"x-paystack-signature": _sign(body, secret)},
        )

    assert response.status_code == 200
    mock_post.assert_awaited_once()
    kwargs = mock_post.await_args.kwargs
    assert kwargs["path"] == "/internal/wallet/confirm-topup"
    assert kwargs["json"]["topup_reference"] == topup_ref
    assert kwargs["json"]["status"] == "completed"
