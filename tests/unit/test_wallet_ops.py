"""Unit tests for wallet_ops core business logic.

Tests call wallet_ops functions directly with the db_session fixture.
No HTTP layer involved — pure business logic validation.
"""

import uuid

import pytest
from fastapi import HTTPException
from services.wallet_service.models import (
    TransactionDirection,
    TransactionStatus,
    TransactionType,
    Wallet,
    WalletStatus,
)
from services.wallet_service.services.wallet_ops import (
    check_balance,
    create_wallet,
    credit_wallet,
    debit_wallet,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_active_wallet(db, balance=100, auth_id=None):
    """Insert an active wallet directly and return it."""
    auth_id = auth_id or f"auth-{uuid.uuid4().hex[:8]}"
    wallet = Wallet(
        member_id=uuid.uuid4(),
        member_auth_id=auth_id,
        balance=balance,
        status=WalletStatus.ACTIVE,
        lifetime_bubbles_purchased=balance,
        lifetime_bubbles_spent=0,
        lifetime_bubbles_received=0,
    )
    db.add(wallet)
    await db.commit()
    await db.refresh(wallet)
    return wallet


# ---------------------------------------------------------------------------
# create_wallet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_wallet_starts_with_zero_balance(db_session):
    """Wallet creation starts at zero without implicit bonus grant."""
    member_id = uuid.uuid4()
    auth_id = f"auth-{uuid.uuid4().hex[:8]}"

    wallet = await create_wallet(
        db_session,
        member_id=member_id,
        member_auth_id=auth_id,
    )

    assert wallet.balance == 0
    assert wallet.lifetime_bubbles_received == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_wallet_idempotent(db_session):
    """Creating a wallet twice for the same member returns the existing one."""
    member_id = uuid.uuid4()
    auth_id = f"auth-{uuid.uuid4().hex[:8]}"

    wallet1 = await create_wallet(
        db_session, member_id=member_id, member_auth_id=auth_id
    )
    wallet2 = await create_wallet(
        db_session, member_id=member_id, member_auth_id=auth_id
    )

    assert wallet1.id == wallet2.id
    assert wallet1.balance == wallet2.balance


# ---------------------------------------------------------------------------
# debit_wallet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_debit_wallet_success(db_session):
    """Successful debit decreases balance and creates transaction."""
    wallet = await _make_active_wallet(db_session, balance=100)

    txn = await debit_wallet(
        db_session,
        member_auth_id=wallet.member_auth_id,
        amount=30,
        idempotency_key=f"debit-{uuid.uuid4().hex[:8]}",
        transaction_type=TransactionType.PURCHASE,
        description="Test purchase",
        service_source="test",
    )

    assert txn.direction == TransactionDirection.DEBIT
    assert txn.amount == 30
    assert txn.balance_before == 100
    assert txn.balance_after == 70
    assert txn.status == TransactionStatus.COMPLETED

    await db_session.refresh(wallet)
    assert wallet.balance == 70
    assert wallet.lifetime_bubbles_spent == 30


@pytest.mark.asyncio
@pytest.mark.unit
async def test_debit_wallet_insufficient_balance(db_session):
    """Debit more than available balance raises HTTPException 400."""
    wallet = await _make_active_wallet(db_session, balance=10)

    with pytest.raises(HTTPException) as exc_info:
        await debit_wallet(
            db_session,
            member_auth_id=wallet.member_auth_id,
            amount=50,
            idempotency_key=f"debit-{uuid.uuid4().hex[:8]}",
            transaction_type=TransactionType.PURCHASE,
            description="Too expensive",
            service_source="test",
        )

    assert exc_info.value.status_code == 400
    assert "Not enough Bubbles" in exc_info.value.detail


@pytest.mark.asyncio
@pytest.mark.unit
async def test_debit_wallet_frozen(db_session):
    """Debit on a frozen wallet raises HTTPException 400."""
    wallet = await _make_active_wallet(db_session, balance=100)
    wallet.status = WalletStatus.FROZEN
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await debit_wallet(
            db_session,
            member_auth_id=wallet.member_auth_id,
            amount=10,
            idempotency_key=f"debit-{uuid.uuid4().hex[:8]}",
            transaction_type=TransactionType.PURCHASE,
            description="Should fail",
            service_source="test",
        )

    assert exc_info.value.status_code == 400
    assert "suspended" in exc_info.value.detail


@pytest.mark.asyncio
@pytest.mark.unit
async def test_debit_wallet_idempotency(db_session):
    """Replaying the same idempotency key returns the existing transaction."""
    wallet = await _make_active_wallet(db_session, balance=100)
    key = f"debit-{uuid.uuid4().hex[:8]}"

    txn1 = await debit_wallet(
        db_session,
        member_auth_id=wallet.member_auth_id,
        amount=20,
        idempotency_key=key,
        transaction_type=TransactionType.PURCHASE,
        description="First",
        service_source="test",
    )
    txn2 = await debit_wallet(
        db_session,
        member_auth_id=wallet.member_auth_id,
        amount=20,
        idempotency_key=key,
        transaction_type=TransactionType.PURCHASE,
        description="Replay",
        service_source="test",
    )

    assert txn1.id == txn2.id

    # Balance should only be debited once
    await db_session.refresh(wallet)
    assert wallet.balance == 80


# ---------------------------------------------------------------------------
# credit_wallet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_credit_wallet_success(db_session):
    """Successful credit increases balance and creates transaction."""
    wallet = await _make_active_wallet(db_session, balance=50)

    txn = await credit_wallet(
        db_session,
        member_auth_id=wallet.member_auth_id,
        amount=25,
        idempotency_key=f"credit-{uuid.uuid4().hex[:8]}",
        transaction_type=TransactionType.TOPUP,
        description="Top-up",
        service_source="test",
    )

    assert txn.direction == TransactionDirection.CREDIT
    assert txn.amount == 25
    assert txn.balance_before == 50
    assert txn.balance_after == 75

    await db_session.refresh(wallet)
    assert wallet.balance == 75
    assert wallet.lifetime_bubbles_purchased == 75  # 50 original + 25 topup


@pytest.mark.asyncio
@pytest.mark.unit
async def test_credit_wallet_frozen_receives_credit(db_session):
    """Frozen wallets can still receive credits (for refunds)."""
    wallet = await _make_active_wallet(db_session, balance=50)
    wallet.status = WalletStatus.FROZEN
    await db_session.commit()

    txn = await credit_wallet(
        db_session,
        member_auth_id=wallet.member_auth_id,
        amount=10,
        idempotency_key=f"credit-{uuid.uuid4().hex[:8]}",
        transaction_type=TransactionType.REFUND,
        description="Refund to frozen wallet",
        service_source="test",
    )

    assert txn.balance_after == 60
    await db_session.refresh(wallet)
    assert wallet.balance == 60


@pytest.mark.asyncio
@pytest.mark.unit
async def test_credit_wallet_idempotency(db_session):
    """Replaying the same idempotency key returns the existing transaction."""
    wallet = await _make_active_wallet(db_session, balance=50)
    key = f"credit-{uuid.uuid4().hex[:8]}"

    txn1 = await credit_wallet(
        db_session,
        member_auth_id=wallet.member_auth_id,
        amount=30,
        idempotency_key=key,
        transaction_type=TransactionType.TOPUP,
        description="First",
        service_source="test",
    )
    txn2 = await credit_wallet(
        db_session,
        member_auth_id=wallet.member_auth_id,
        amount=30,
        idempotency_key=key,
        transaction_type=TransactionType.TOPUP,
        description="Replay",
        service_source="test",
    )

    assert txn1.id == txn2.id
    await db_session.refresh(wallet)
    assert wallet.balance == 80  # only credited once


# ---------------------------------------------------------------------------
# check_balance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_check_balance_sufficient(db_session):
    """Check balance returns True when wallet has enough."""
    wallet = await _make_active_wallet(db_session, balance=100)

    sufficient, balance, wallet_status = await check_balance(
        db_session, wallet.member_auth_id, 50
    )

    assert sufficient is True
    assert balance == 100
    assert wallet_status == WalletStatus.ACTIVE


@pytest.mark.asyncio
@pytest.mark.unit
async def test_check_balance_insufficient(db_session):
    """Check balance returns False when wallet doesn't have enough."""
    wallet = await _make_active_wallet(db_session, balance=10)

    sufficient, balance, wallet_status = await check_balance(
        db_session, wallet.member_auth_id, 50
    )

    assert sufficient is False
    assert balance == 10
