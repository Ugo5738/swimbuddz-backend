"""Integration tests for wallet_service admin endpoints."""

import uuid

import pytest
from services.wallet_service.models import WalletStatus
from tests.factories import WalletFactory


# ---------------------------------------------------------------------------
# Admin wallet list / detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_list_wallets(wallet_client, db_session):
    """GET /admin/wallet/wallets — paginated list of all wallets."""
    # Create a wallet directly
    w = WalletFactory.create()
    db_session.add(w)
    await db_session.commit()

    response = await wallet_client.get("/admin/wallet/wallets")
    assert response.status_code == 200
    data = response.json()
    assert "wallets" in data
    assert data["total"] >= 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_get_wallet(wallet_client, db_session):
    """GET /admin/wallet/wallets/{id} — wallet details."""
    w = WalletFactory.create()
    db_session.add(w)
    await db_session.commit()

    response = await wallet_client.get(f"/admin/wallet/wallets/{w.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(w.id)


# ---------------------------------------------------------------------------
# Freeze / Unfreeze
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_freeze_wallet(wallet_client, db_session):
    """POST /admin/wallet/wallets/{id}/freeze — freezes wallet + audit log."""
    w = WalletFactory.create(status=WalletStatus.ACTIVE)
    db_session.add(w)
    await db_session.commit()

    response = await wallet_client.post(
        f"/admin/wallet/wallets/{w.id}/freeze",
        json={"reason": "Suspicious activity on account"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "frozen"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_unfreeze_wallet(wallet_client, db_session):
    """POST /admin/wallet/wallets/{id}/unfreeze — unfreezes wallet."""
    w = WalletFactory.create(status=WalletStatus.FROZEN)
    db_session.add(w)
    await db_session.commit()

    response = await wallet_client.post(
        f"/admin/wallet/wallets/{w.id}/unfreeze",
        json={"reason": "Investigation complete, account cleared"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "active"


# ---------------------------------------------------------------------------
# Adjust balance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_adjust_balance(wallet_client, db_session):
    """POST /admin/wallet/wallets/{id}/adjust — adjusts balance + audit log."""
    w = WalletFactory.create(balance=100)
    db_session.add(w)
    await db_session.commit()

    # Credit adjustment
    response = await wallet_client.post(
        f"/admin/wallet/wallets/{w.id}/adjust",
        json={"amount": 50, "reason": "Compensation for service issue"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["balance"] == 150


# ---------------------------------------------------------------------------
# Promotional grants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_grant_promotional(wallet_client, db_session):
    """POST /admin/wallet/grants — issue promotional Bubbles."""
    # First create a wallet for the target member
    w = WalletFactory.create(balance=50)
    db_session.add(w)
    await db_session.commit()

    response = await wallet_client.post(
        "/admin/wallet/grants",
        json={
            "member_auth_id": w.member_auth_id,
            "bubbles_amount": 25,
            "grant_type": "promotional",
            "reason": "Community event participation reward",
        },
    )
    assert response.status_code in (200, 201), response.text
    data = response.json()
    assert data["bubbles_amount"] == 25


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_stats(wallet_client, db_session):
    """GET /admin/wallet/stats — system-wide statistics."""
    # Create some wallets for stats
    for i in range(3):
        db_session.add(WalletFactory.create())
    await db_session.commit()

    response = await wallet_client.get("/admin/wallet/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total_wallets" in data
    assert "active_wallets" in data
    assert data["total_wallets"] >= 3


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_audit_log(wallet_client, db_session):
    """GET /admin/wallet/audit-log — returns entries after admin actions."""
    # Create wallet and freeze it (which creates an audit log entry)
    w = WalletFactory.create(status=WalletStatus.ACTIVE)
    db_session.add(w)
    await db_session.commit()

    await wallet_client.post(
        f"/admin/wallet/wallets/{w.id}/freeze",
        json={"reason": "Test freeze for audit log"},
    )

    response = await wallet_client.get("/admin/wallet/audit-log")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["entries"]) >= 1
