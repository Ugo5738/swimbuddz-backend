"""Unit tests for wallet-hold capture and legacy Bubble debit fallback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from services.payments_service.routers.intents._helpers import _debit_bubbles


def _payment(metadata: dict) -> SimpleNamespace:
    return SimpleNamespace(
        member_auth_id="auth-user-1",
        reference="PAY-123",
        payment_metadata=metadata,
    )


def _client_returning(response: httpx.Response) -> SimpleNamespace:
    return SimpleNamespace(post=AsyncMock(return_value=response))


@pytest.mark.asyncio
@pytest.mark.unit
async def test_debit_bubbles_posts_expected_payload_and_records_txn():
    """5 Bubbles → debit posted with the right key/amount, txn recorded."""
    response = httpx.Response(
        status_code=200,
        json={"success": True, "transaction_id": "txn-abc", "balance_after": 10},
        request=httpx.Request("POST", "http://wallet/internal/wallet/debit"),
    )
    client = _client_returning(response)
    payment = _payment({"bubbles_to_apply": 5})

    txn_id = await _debit_bubbles(client, payment, reference_type="session_booking")

    client.post.assert_awaited_once()
    body = client.post.await_args.kwargs["json"]
    assert body["idempotency_key"] == "session_booking_PAY-123"
    assert body["amount"] == 5
    assert body["reference_type"] == "session_booking"
    assert body["member_auth_id"] == "auth-user-1"
    # Returns the txn id and records it on the payment metadata for audit.
    assert txn_id == "txn-abc"
    assert payment.payment_metadata["wallet_transaction_id"] == "txn-abc"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_new_payment_captures_wallet_hold_and_records_transaction():
    response = httpx.Response(
        status_code=200,
        json={
            "id": "hold-1",
            "status": "captured",
            "wallet_transaction_id": "txn-captured",
        },
        request=httpx.Request(
            "POST", "http://wallet/internal/wallet/holds/hold-1/capture"
        ),
    )
    client = _client_returning(response)
    payment = _payment({"bubbles_to_apply": 5, "wallet_hold_id": "hold-1"})

    txn_id = await _debit_bubbles(client, payment, reference_type="session_booking")

    url = client.post.await_args.args[0]
    assert url.endswith("/internal/wallet/holds/hold-1/capture")
    assert "json" not in client.post.await_args.kwargs
    assert txn_id == "txn-captured"
    assert payment.payment_metadata["wallet_hold_status"] == "captured"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_debit_bubbles_noop_when_no_bubbles():
    """No Bubbles applied → no wallet call, returns None."""
    client = _client_returning(
        httpx.Response(200, request=httpx.Request("POST", "http://wallet"))
    )
    payment = _payment({"bubbles_to_apply": 0})

    txn_id = await _debit_bubbles(client, payment, reference_type="session_fee")

    client.post.assert_not_awaited()
    assert txn_id is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_debit_bubbles_idempotent_when_already_debited():
    """Replay after a prior successful debit must not double-charge."""
    client = _client_returning(
        httpx.Response(200, request=httpx.Request("POST", "http://wallet"))
    )
    payment = _payment({"bubbles_to_apply": 5, "wallet_transaction_id": "txn-existing"})

    txn_id = await _debit_bubbles(client, payment, reference_type="ride_share")

    client.post.assert_not_awaited()
    assert txn_id == "txn-existing"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_debit_bubbles_fails_closed_on_failure():
    response = httpx.Response(
        status_code=400,
        text="Insufficient balance",
        request=httpx.Request("POST", "http://wallet/internal/wallet/debit"),
    )
    client = _client_returning(response)
    payment = _payment({"bubbles_to_apply": 5})

    with pytest.raises(Exception) as exc:
        await _debit_bubbles(client, payment, reference_type="session_bundle")

    assert getattr(exc.value, "status_code", None) == 502
    assert payment.payment_metadata["bubbles_debit_failed"] is True
    assert "wallet_transaction_id" not in payment.payment_metadata
