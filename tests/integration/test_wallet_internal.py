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
    assert data["balance"] == 0
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


# ---------------------------------------------------------------------------
# GET /internal/wallet/ecosystem-stats — flywheel reporting aggregates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_ecosystem_stats_empty_window(wallet_client, db_session):
    """GET /internal/wallet/ecosystem-stats — empty window returns zeros."""
    response = await wallet_client.get(
        "/internal/wallet/ecosystem-stats",
        params={"from": "2030-01-01", "to": "2030-01-07"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["active_wallet_users"] == 0
    assert data["single_service_users"] == 0
    assert data["cross_service_users"] == 0
    assert data["total_bubbles_spent"] == 0
    assert data["total_topup_bubbles"] == 0
    assert data["spend_distribution"] == {}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_ecosystem_stats_classifies_users_and_spend(
    wallet_client, db_session
):
    """Cross-service vs single-service classification + spend distribution.

    Wallet A: spends in 2 services (academy + sessions) → cross-service.
    Wallet B: spends in 1 service (sessions) + a top-up → single-service.
    Wallet C: only top-up, no debits → active but single-service.
    """
    from datetime import datetime, timezone

    from services.wallet_service.models import (
        TransactionDirection,
        TransactionStatus,
        TransactionType,
    )
    from tests.factories import WalletFactory, WalletTransactionFactory

    window_day = datetime(2030, 6, 15, 12, 0, tzinfo=timezone.utc)

    wallet_a = WalletFactory.create(balance=500)
    wallet_b = WalletFactory.create(balance=500)
    wallet_c = WalletFactory.create(balance=500)
    db_session.add_all([wallet_a, wallet_b, wallet_c])
    await db_session.commit()

    txns = [
        # Wallet A — two distinct service_sources on DEBITs
        WalletTransactionFactory.create(
            wallet_id=wallet_a.id,
            direction=TransactionDirection.DEBIT,
            transaction_type=TransactionType.PURCHASE,
            status=TransactionStatus.COMPLETED,
            amount=100,
            service_source="academy_service",
            created_at=window_day,
        ),
        WalletTransactionFactory.create(
            wallet_id=wallet_a.id,
            direction=TransactionDirection.DEBIT,
            transaction_type=TransactionType.PURCHASE,
            status=TransactionStatus.COMPLETED,
            amount=50,
            service_source="sessions_service",
            created_at=window_day,
        ),
        # Wallet B — single DEBIT service + a top-up CREDIT
        WalletTransactionFactory.create(
            wallet_id=wallet_b.id,
            direction=TransactionDirection.DEBIT,
            transaction_type=TransactionType.PURCHASE,
            status=TransactionStatus.COMPLETED,
            amount=150,
            service_source="sessions_service",
            created_at=window_day,
        ),
        WalletTransactionFactory.create(
            wallet_id=wallet_b.id,
            direction=TransactionDirection.CREDIT,
            transaction_type=TransactionType.TOPUP,
            status=TransactionStatus.COMPLETED,
            amount=200,
            service_source=None,
            created_at=window_day,
        ),
        # Wallet C — only top-up, no debit; still active in window
        WalletTransactionFactory.create(
            wallet_id=wallet_c.id,
            direction=TransactionDirection.CREDIT,
            transaction_type=TransactionType.TOPUP,
            status=TransactionStatus.COMPLETED,
            amount=300,
            service_source=None,
            created_at=window_day,
        ),
    ]
    db_session.add_all(txns)
    await db_session.commit()

    response = await wallet_client.get(
        "/internal/wallet/ecosystem-stats",
        params={"from": "2030-06-01", "to": "2030-06-30"},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["active_wallet_users"] == 3
    assert data["cross_service_users"] == 1  # only wallet A
    assert data["single_service_users"] == 2  # wallets B and C
    assert data["total_bubbles_spent"] == 100 + 50 + 150  # 300
    assert data["total_topup_bubbles"] == 200 + 300  # 500

    dist = data["spend_distribution"]
    assert pytest.approx(dist["academy_service"], rel=1e-6) == 100 / 300
    assert pytest.approx(dist["sessions_service"], rel=1e-6) == 200 / 300
    assert pytest.approx(sum(dist.values()), rel=1e-6) == 1.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_ecosystem_stats_excludes_pending_and_out_of_window(
    wallet_client, db_session
):
    """Pending txns and out-of-window txns must not be counted."""
    from datetime import datetime, timezone

    from services.wallet_service.models import (
        TransactionDirection,
        TransactionStatus,
        TransactionType,
    )
    from tests.factories import WalletFactory, WalletTransactionFactory

    in_window = datetime(2030, 7, 15, 12, 0, tzinfo=timezone.utc)
    out_of_window = datetime(2029, 12, 31, 23, 0, tzinfo=timezone.utc)

    wallet = WalletFactory.create(balance=500)
    db_session.add(wallet)
    await db_session.commit()

    db_session.add_all(
        [
            # Pending — must be excluded
            WalletTransactionFactory.create(
                wallet_id=wallet.id,
                direction=TransactionDirection.DEBIT,
                transaction_type=TransactionType.PURCHASE,
                status=TransactionStatus.PENDING,
                amount=999,
                service_source="academy_service",
                created_at=in_window,
            ),
            # Out of window — must be excluded
            WalletTransactionFactory.create(
                wallet_id=wallet.id,
                direction=TransactionDirection.DEBIT,
                transaction_type=TransactionType.PURCHASE,
                status=TransactionStatus.COMPLETED,
                amount=999,
                service_source="academy_service",
                created_at=out_of_window,
            ),
        ]
    )
    await db_session.commit()

    response = await wallet_client.get(
        "/internal/wallet/ecosystem-stats",
        params={"from": "2030-07-01", "to": "2030-07-31"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["active_wallet_users"] == 0
    assert data["total_bubbles_spent"] == 0
    assert data["spend_distribution"] == {}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_internal_ecosystem_stats_null_service_source_bucketed(
    wallet_client, db_session
):
    """NULL service_source on DEBITs collapses to a single 'uncategorized' bucket."""
    from datetime import datetime, timezone

    from services.wallet_service.models import (
        TransactionDirection,
        TransactionStatus,
        TransactionType,
    )
    from tests.factories import WalletFactory, WalletTransactionFactory

    window_day = datetime(2030, 8, 10, 12, 0, tzinfo=timezone.utc)
    wallet = WalletFactory.create(balance=500)
    db_session.add(wallet)
    await db_session.commit()

    db_session.add_all(
        [
            WalletTransactionFactory.create(
                wallet_id=wallet.id,
                direction=TransactionDirection.DEBIT,
                transaction_type=TransactionType.PURCHASE,
                status=TransactionStatus.COMPLETED,
                amount=40,
                service_source=None,
                created_at=window_day,
            ),
            WalletTransactionFactory.create(
                wallet_id=wallet.id,
                direction=TransactionDirection.DEBIT,
                transaction_type=TransactionType.PURCHASE,
                status=TransactionStatus.COMPLETED,
                amount=60,
                service_source=None,
                created_at=window_day,
            ),
        ]
    )
    await db_session.commit()

    response = await wallet_client.get(
        "/internal/wallet/ecosystem-stats",
        params={"from": "2030-08-01", "to": "2030-08-31"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    # NULL collapses to one bucket → only one distinct service → not cross-service.
    assert data["cross_service_users"] == 0
    assert data["single_service_users"] == 1
    assert data["total_bubbles_spent"] == 100
    assert data["spend_distribution"] == {"uncategorized": 1.0}
