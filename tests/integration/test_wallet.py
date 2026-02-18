"""Integration tests for wallet_service member endpoints."""

import uuid

import pytest
from tests.conftest import make_member_user, override_auth


# ---------------------------------------------------------------------------
# Wallet CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_wallet_endpoint(wallet_client, db_session):
    """POST /wallet/create — creates wallet with zero starting balance."""
    response = await wallet_client.post("/wallet/create")

    assert response.status_code == 201, response.text
    data = response.json()
    assert data["balance"] == 0
    assert data["status"] == "active"
    assert "id" in data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_my_wallet(wallet_client, db_session):
    """GET /wallet/me — returns wallet after creation."""
    # First create a wallet
    create_resp = await wallet_client.post("/wallet/create")
    assert create_resp.status_code == 201

    response = await wallet_client.get("/wallet/me")
    assert response.status_code == 200
    data = response.json()
    assert data["balance"] == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_my_wallet_not_found(wallet_client, db_session):
    """GET /wallet/me — 404 when no wallet exists."""
    # Use a user who has never created a wallet
    from services.wallet_service.app.main import app

    user = make_member_user(user_id=f"no-wallet-{uuid.uuid4().hex[:8]}")
    with override_auth(app, user):
        response = await wallet_client.get("/wallet/me")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_transactions_empty(wallet_client, db_session):
    """GET /wallet/transactions — new wallet has no transactions yet."""
    await wallet_client.post("/wallet/create")

    response = await wallet_client.get("/wallet/transactions")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert len(data["transactions"]) == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_transactions_with_data(wallet_client, db_session):
    """GET /wallet/transactions — shows transactions after credit + debit."""
    await wallet_client.post("/wallet/create")

    credit_resp = await wallet_client.post(
        "/wallet/credit",
        json={
            "idempotency_key": f"seed-{uuid.uuid4().hex[:8]}",
            "member_auth_id": "ignored",
            "amount": 20,
            "transaction_type": "refund",
            "description": "Seed funds for debit test",
            "service_source": "test",
        },
    )
    assert credit_resp.status_code == 200, credit_resp.text

    # Debit some Bubbles
    debit_resp = await wallet_client.post(
        "/wallet/debit",
        json={
            "idempotency_key": f"test-{uuid.uuid4().hex[:8]}",
            "member_auth_id": "ignored",  # overridden by auth
            "amount": 5,
            "transaction_type": "purchase",
            "description": "Test purchase",
            "service_source": "test",
        },
    )
    assert debit_resp.status_code == 200, debit_resp.text

    response = await wallet_client.get("/wallet/transactions")
    data = response.json()
    assert data["total"] == 2  # credit + debit


# ---------------------------------------------------------------------------
# Debit / Credit / Balance check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_debit_endpoint(wallet_client, db_session):
    """POST /wallet/debit — decreases balance."""
    await wallet_client.post("/wallet/create")
    seed_resp = await wallet_client.post(
        "/wallet/credit",
        json={
            "idempotency_key": f"seed-{uuid.uuid4().hex[:8]}",
            "member_auth_id": "ignored",
            "amount": 20,
            "transaction_type": "refund",
            "description": "Seed funds for debit endpoint",
            "service_source": "test",
        },
    )
    assert seed_resp.status_code == 200, seed_resp.text

    response = await wallet_client.post(
        "/wallet/debit",
        json={
            "idempotency_key": f"test-{uuid.uuid4().hex[:8]}",
            "member_auth_id": "ignored",
            "amount": 5,
            "transaction_type": "purchase",
            "description": "Buy swim cap",
            "service_source": "store_service",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["balance_after"] == 15  # 20 seeded - 5 debit


@pytest.mark.asyncio
@pytest.mark.integration
async def test_credit_endpoint(wallet_client, db_session):
    """POST /wallet/credit — increases balance."""
    await wallet_client.post("/wallet/create")

    response = await wallet_client.post(
        "/wallet/credit",
        json={
            "idempotency_key": f"test-{uuid.uuid4().hex[:8]}",
            "member_auth_id": "ignored",
            "amount": 20,
            "transaction_type": "refund",
            "description": "Refund for cancelled session",
            "service_source": "sessions_service",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["success"] is True
    assert data["balance_after"] == 20


@pytest.mark.asyncio
@pytest.mark.integration
async def test_check_balance_endpoint(wallet_client, db_session):
    """POST /wallet/check-balance — returns sufficient flag."""
    await wallet_client.post("/wallet/create")
    seed_resp = await wallet_client.post(
        "/wallet/credit",
        json={
            "idempotency_key": f"seed-{uuid.uuid4().hex[:8]}",
            "member_auth_id": "ignored",
            "amount": 20,
            "transaction_type": "refund",
            "description": "Seed funds for balance check",
            "service_source": "test",
        },
    )
    assert seed_resp.status_code == 200, seed_resp.text

    # Sufficient
    response = await wallet_client.post(
        "/wallet/check-balance",
        json={"member_auth_id": "ignored", "required_amount": 5},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sufficient"] is True
    assert data["current_balance"] == 20

    # Insufficient
    response = await wallet_client.post(
        "/wallet/check-balance",
        json={"member_auth_id": "ignored", "required_amount": 500},
    )
    data = response.json()
    assert data["sufficient"] is False
