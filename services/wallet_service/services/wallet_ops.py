"""Core wallet operations — atomic debit/credit with idempotency and row-level locking."""

import uuid
from typing import Optional

from fastapi import HTTPException, status
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from services.wallet_service.models import (
    GrantType,
    PromotionalBubbleGrant,
    TransactionDirection,
    TransactionStatus,
    TransactionType,
    Wallet,
    WalletStatus,
    WalletTransaction,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Wallet config constants (from design doc Section 8)
# ---------------------------------------------------------------------------
NAIRA_PER_BUBBLE = 100
WELCOME_BONUS_BUBBLES = 10
WELCOME_BONUS_ENABLED = True


# ---------------------------------------------------------------------------
# Wallet creation
# ---------------------------------------------------------------------------


async def create_wallet(
    db: AsyncSession,
    *,
    member_id: uuid.UUID,
    member_auth_id: str,
) -> Wallet:
    """Create a wallet for a new member.

    Idempotent — returns existing wallet if one already exists for this member.
    """
    # Check for existing wallet
    result = await db.execute(
        select(Wallet).where(Wallet.member_auth_id == member_auth_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    wallet = Wallet(
        member_id=member_id,
        member_auth_id=member_auth_id,
        balance=0,
        status=WalletStatus.ACTIVE,
    )
    db.add(wallet)
    await db.flush()

    await db.commit()
    await db.refresh(wallet)

    logger.info(
        "Created wallet %s for member %s (balance=%d)",
        wallet.id,
        member_auth_id,
        wallet.balance,
    )
    return wallet


async def grant_welcome_bonus_if_eligible(
    db: AsyncSession,
    *,
    member_id: uuid.UUID,
    member_auth_id: str,
    eligible: bool,
    granted_by: str = "system",
    reason: Optional[str] = None,
) -> tuple[Wallet, bool]:
    """Grant the one-time welcome bonus after paid activation if eligible.

    Returns ``(wallet, granted)`` where granted=False means already granted or ineligible.
    """
    wallet = await create_wallet(
        db,
        member_id=member_id,
        member_auth_id=member_auth_id,
    )

    if not eligible or not WELCOME_BONUS_ENABLED or WELCOME_BONUS_BUBBLES <= 0:
        return wallet, False

    idempotency_key = f"welcome-bonus-{member_auth_id}"
    existing_txn = await db.execute(
        select(WalletTransaction).where(
            WalletTransaction.idempotency_key == idempotency_key
        )
    )
    if existing_txn.scalar_one_or_none():
        return wallet, False

    grant_reason = reason or "Welcome bonus — thanks for joining SwimBuddz!"
    balance_before = wallet.balance
    balance_after = balance_before + WELCOME_BONUS_BUBBLES

    grant = PromotionalBubbleGrant(
        wallet_id=wallet.id,
        member_auth_id=member_auth_id,
        grant_type=GrantType.WELCOME_BONUS,
        bubbles_amount=WELCOME_BONUS_BUBBLES,
        bubbles_remaining=WELCOME_BONUS_BUBBLES,
        reason=grant_reason,
        granted_by=granted_by,
    )
    db.add(grant)
    await db.flush()

    txn = WalletTransaction(
        wallet_id=wallet.id,
        idempotency_key=idempotency_key,
        transaction_type=TransactionType.WELCOME_BONUS,
        direction=TransactionDirection.CREDIT,
        amount=WELCOME_BONUS_BUBBLES,
        balance_before=balance_before,
        balance_after=balance_after,
        status=TransactionStatus.COMPLETED,
        description=f"{grant_reason} ({WELCOME_BONUS_BUBBLES} Bubbles)",
        service_source="wallet_service",
        reference_type="grant",
        reference_id=str(grant.id),
        initiated_by=granted_by,
    )
    db.add(txn)
    await db.flush()

    grant.transaction_id = txn.id
    wallet.balance = balance_after
    wallet.lifetime_bubbles_received += WELCOME_BONUS_BUBBLES
    wallet.updated_at = utc_now()

    await db.commit()
    await db.refresh(wallet)
    logger.info(
        "Granted welcome bonus to member %s wallet=%s amount=%d",
        member_auth_id,
        wallet.id,
        WELCOME_BONUS_BUBBLES,
    )
    return wallet, True


# ---------------------------------------------------------------------------
# Debit (atomic)
# ---------------------------------------------------------------------------


async def debit_wallet(
    db: AsyncSession,
    *,
    member_auth_id: str,
    amount: int,
    idempotency_key: str,
    transaction_type: TransactionType,
    description: str,
    service_source: str,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    initiated_by: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> WalletTransaction:
    """Atomically debit a wallet following design Section 7.1.

    1. Check idempotency — return existing transaction if key exists
    2. SELECT FOR UPDATE on wallet row
    3. Validate wallet active + sufficient balance
    4. Create transaction record with balance snapshots
    5. Update wallet balance + lifetime counters
    6. Commit atomically
    """
    # 1. Idempotency check
    result = await db.execute(
        select(WalletTransaction).where(
            WalletTransaction.idempotency_key == idempotency_key
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        logger.info(
            "Idempotent replay for key=%s → txn=%s", idempotency_key, existing.id
        )
        return existing

    # 2. Lock wallet row
    result = await db.execute(
        select(Wallet).where(Wallet.member_auth_id == member_auth_id).with_for_update()
    )
    wallet = result.scalar_one_or_none()
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found",
        )

    # 3. Validate
    if wallet.status != WalletStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wallet temporarily suspended",
        )
    if wallet.balance < amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not enough Bubbles. You need {amount} 🫧 but have {wallet.balance} 🫧.",
        )

    # 4. Create transaction
    balance_before = wallet.balance
    balance_after = wallet.balance - amount

    txn = WalletTransaction(
        wallet_id=wallet.id,
        idempotency_key=idempotency_key,
        transaction_type=transaction_type,
        direction=TransactionDirection.DEBIT,
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_after,
        status=TransactionStatus.COMPLETED,
        description=description,
        service_source=service_source,
        reference_type=reference_type,
        reference_id=reference_id,
        initiated_by=initiated_by,
        txn_metadata=metadata,
    )
    db.add(txn)

    # 5. Update wallet
    wallet.balance = balance_after
    wallet.lifetime_bubbles_spent += amount
    wallet.updated_at = utc_now()

    # 6. Commit
    await db.commit()
    await db.refresh(txn)

    logger.info(
        "Debit %d from wallet %s (key=%s), balance %d→%d",
        amount,
        wallet.id,
        idempotency_key,
        balance_before,
        balance_after,
    )
    return txn


# ---------------------------------------------------------------------------
# Credit (atomic)
# ---------------------------------------------------------------------------


async def credit_wallet(
    db: AsyncSession,
    *,
    member_auth_id: str,
    amount: int,
    idempotency_key: str,
    transaction_type: TransactionType,
    description: str,
    service_source: str,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    initiated_by: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> WalletTransaction:
    """Atomically credit a wallet. Same pattern as debit but adds balance.

    Note: Frozen wallets can still receive credits (for refunds).
    """
    # 1. Idempotency check
    result = await db.execute(
        select(WalletTransaction).where(
            WalletTransaction.idempotency_key == idempotency_key
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        logger.info(
            "Idempotent replay for key=%s → txn=%s", idempotency_key, existing.id
        )
        return existing

    # 2. Lock wallet row
    result = await db.execute(
        select(Wallet).where(Wallet.member_auth_id == member_auth_id).with_for_update()
    )
    wallet = result.scalar_one_or_none()
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found",
        )

    # 3. Create transaction
    balance_before = wallet.balance
    balance_after = wallet.balance + amount

    txn = WalletTransaction(
        wallet_id=wallet.id,
        idempotency_key=idempotency_key,
        transaction_type=transaction_type,
        direction=TransactionDirection.CREDIT,
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_after,
        status=TransactionStatus.COMPLETED,
        description=description,
        service_source=service_source,
        reference_type=reference_type,
        reference_id=reference_id,
        initiated_by=initiated_by,
        txn_metadata=metadata,
    )
    db.add(txn)

    # 4. Update wallet + appropriate lifetime counter
    wallet.balance = balance_after
    if transaction_type == TransactionType.TOPUP:
        wallet.lifetime_bubbles_purchased += amount
    else:
        wallet.lifetime_bubbles_received += amount
    wallet.updated_at = utc_now()

    # 5. Commit
    await db.commit()
    await db.refresh(txn)

    logger.info(
        "Credit %d to wallet %s (key=%s), balance %d→%d",
        amount,
        wallet.id,
        idempotency_key,
        balance_before,
        balance_after,
    )
    return txn


# ---------------------------------------------------------------------------
# Balance check (read-only)
# ---------------------------------------------------------------------------


async def get_wallet_by_auth_id(db: AsyncSession, member_auth_id: str) -> Wallet:
    """Get wallet by member auth ID. Raises 404 if not found."""
    result = await db.execute(
        select(Wallet).where(Wallet.member_auth_id == member_auth_id)
    )
    wallet = result.scalar_one_or_none()
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found",
        )
    return wallet


async def get_wallet_by_id(db: AsyncSession, wallet_id: uuid.UUID) -> Wallet:
    """Get wallet by wallet ID. Raises 404 if not found."""
    result = await db.execute(select(Wallet).where(Wallet.id == wallet_id))
    wallet = result.scalar_one_or_none()
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found",
        )
    return wallet


async def check_balance(
    db: AsyncSession, member_auth_id: str, required_amount: int
) -> tuple[bool, int, WalletStatus]:
    """Check if wallet has sufficient balance. Non-destructive read."""
    wallet = await get_wallet_by_auth_id(db, member_auth_id)
    return (wallet.balance >= required_amount, wallet.balance, wallet.status)
