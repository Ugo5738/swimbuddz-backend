"""Integration tests for wallet_service internal (service-to-service) endpoints."""

import uuid

import pytest
from tests.factories import WalletFactory


# ---------------------------------------------------------------------------
# Internal wallet operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_create_wallet(wallet_client, db_session):
    """POST /internal/wallet/create — service creates wallet for new member."""
    member_id = str(uuid.uuid4())
    auth_id = f"auth-{uuid.uuid4().hex[:8]}"

    response = await wallet_client.post(
        "/internal/wallet/create",
        json={"member_id": member_id, "member_auth_id": auth_id},
    )
    assert response.status_code in (200, 201), response.text
    data = response.json()
    assert data["balance"] == 10  # welcome bonus
    assert data["member_auth_id"] == auth_id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_debit(wallet_client, db_session):
    """POST /internal/wallet/debit — service-to-service debit."""
    w = WalletFactory.create(balance=100)
    db_session.add(w)
    await db_session.commit()

    response = await wallet_client.post(
        "/internal/wallet/debit",
        json={
            "idempotency_key": f"int-debit-{uuid.uuid4().hex[:8]}",
            "member_auth_id": w.member_auth_id,
            "amount": 25,
            "transaction_type": "purchase",
            "description": "Session payment via sessions_service",
            "service_source": "sessions_service",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["balance_after"] == 75


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_credit(wallet_client, db_session):
    """POST /internal/wallet/credit — service-to-service credit."""
    w = WalletFactory.create(balance=50)
    db_session.add(w)
    await db_session.commit()

    response = await wallet_client.post(
        "/internal/wallet/credit",
        json={
            "idempotency_key": f"int-credit-{uuid.uuid4().hex[:8]}",
            "member_auth_id": w.member_auth_id,
            "amount": 30,
            "transaction_type": "refund",
            "description": "Refund via payments_service",
            "service_source": "payments_service",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["balance_after"] == 80


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_get_balance(wallet_client, db_session):
    """GET /internal/wallet/balance/{auth_id} — returns balance info."""
    w = WalletFactory.create(balance=75)
    db_session.add(w)
    await db_session.commit()

    response = await wallet_client.get(f"/internal/wallet/balance/{w.member_auth_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["balance"] == 75
    assert data["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_check_balance(wallet_client, db_session):
    """POST /internal/wallet/check-balance — returns sufficient/insufficient."""
    w = WalletFactory.create(balance=50)
    db_session.add(w)
    await db_session.commit()

    # Sufficient
    response = await wallet_client.post(
        "/internal/wallet/check-balance",
        json={"member_auth_id": w.member_auth_id, "required_amount": 30},
    )
    assert response.status_code == 200
    assert response.json()["sufficient"] is True

    # Insufficient
    response = await wallet_client.post(
        "/internal/wallet/check-balance",
        json={"member_auth_id": w.member_auth_id, "required_amount": 999},
    )
    assert response.status_code == 200
    assert response.json()["sufficient"] is False
