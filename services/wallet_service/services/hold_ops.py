"""Atomic hold, capture, and release operations for Bubble checkouts."""

import uuid
from datetime import timedelta

from fastapi import HTTPException, status
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from services.wallet_service.models import (
    TransactionDirection,
    TransactionStatus,
    TransactionType,
    Wallet,
    WalletHold,
    WalletHoldStatus,
    WalletStatus,
    WalletTransaction,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .wallet_ops import _consume_promo_grants_fifo

logger = get_logger(__name__)


def _validate_idempotent_hold(
    hold: WalletHold, *, member_auth_id: str, amount: int
) -> None:
    if hold.member_auth_id != member_auth_id or hold.amount != amount:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Wallet hold idempotency key was reused with different values",
        )


async def active_held_amount(
    db: AsyncSession,
    wallet_id: uuid.UUID,
    *,
    exclude_hold_id: uuid.UUID | None = None,
) -> int:
    now = utc_now()
    query = select(func.coalesce(func.sum(WalletHold.amount), 0)).where(
        WalletHold.wallet_id == wallet_id,
        WalletHold.status == WalletHoldStatus.HELD,
        WalletHold.expires_at > now,
    )
    if exclude_hold_id is not None:
        query = query.where(WalletHold.id != exclude_hold_id)
    return int((await db.execute(query)).scalar_one() or 0)


async def available_wallet_balance(db: AsyncSession, wallet: Wallet) -> int:
    return max(wallet.balance - await active_held_amount(db, wallet.id), 0)


async def create_wallet_hold(
    db: AsyncSession,
    *,
    member_auth_id: str,
    amount: int,
    idempotency_key: str,
    description: str,
    service_source: str,
    reference_type: str | None = None,
    reference_id: str | None = None,
    expires_in_seconds: int = 1800,
) -> tuple[WalletHold, int]:
    existing = (
        await db.execute(
            select(WalletHold).where(WalletHold.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        _validate_idempotent_hold(
            existing, member_auth_id=member_auth_id, amount=amount
        )
        wallet = (
            await db.execute(select(Wallet).where(Wallet.id == existing.wallet_id))
        ).scalar_one()
        return existing, await available_wallet_balance(db, wallet)

    wallet = (
        await db.execute(
            select(Wallet)
            .where(Wallet.member_auth_id == member_auth_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if wallet is None:
        raise HTTPException(status_code=404, detail="Wallet not found")
    if wallet.status != WalletStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Wallet temporarily suspended")

    # Re-check after acquiring the wallet lock. Concurrent requests for the
    # same wallet can both pass the optimistic lookup above.
    existing = (
        await db.execute(
            select(WalletHold).where(WalletHold.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        _validate_idempotent_hold(
            existing, member_auth_id=member_auth_id, amount=amount
        )
        return existing, await available_wallet_balance(db, wallet)

    available = await available_wallet_balance(db, wallet)
    if available < amount:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Not enough available Bubbles. You need {amount} but have {available}.",
        )

    hold = WalletHold(
        wallet_id=wallet.id,
        member_auth_id=member_auth_id,
        idempotency_key=idempotency_key,
        amount=amount,
        description=description,
        service_source=service_source,
        reference_type=reference_type,
        reference_id=reference_id,
        expires_at=utc_now() + timedelta(seconds=expires_in_seconds),
    )
    db.add(hold)
    await db.commit()
    await db.refresh(hold)
    logger.info("Held %d Bubbles for %s as %s", amount, member_auth_id, hold.id)
    return hold, available - amount


async def capture_wallet_hold(
    db: AsyncSession, hold_id: uuid.UUID
) -> tuple[WalletHold, int]:
    hold = (
        await db.execute(
            select(WalletHold).where(WalletHold.id == hold_id).with_for_update()
        )
    ).scalar_one_or_none()
    if hold is None:
        raise HTTPException(status_code=404, detail="Wallet hold not found")

    wallet = (
        await db.execute(
            select(Wallet).where(Wallet.id == hold.wallet_id).with_for_update()
        )
    ).scalar_one()
    if hold.status == WalletHoldStatus.CAPTURED:
        return hold, await available_wallet_balance(db, wallet)
    if hold.status == WalletHoldStatus.RELEASED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot capture a {hold.status.value} wallet hold",
        )
    if wallet.status != WalletStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Wallet temporarily suspended")

    # A provider callback can arrive after the reservation window. Re-acquire
    # the amount under the wallet lock when it is still available, so a late
    # successful card payment does not fail fulfillment merely because the
    # checkout took longer than expected. Other active holds remain protected.
    expired = hold.status == WalletHoldStatus.EXPIRED or hold.expires_at <= utc_now()
    other_held = (
        await active_held_amount(db, wallet.id, exclude_hold_id=hold.id)
        if expired
        else 0
    )
    can_capture = (
        wallet.balance - other_held >= hold.amount
        if expired
        else wallet.balance >= hold.amount
    )
    if not can_capture:
        if expired:
            hold.status = WalletHoldStatus.EXPIRED
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Wallet balance no longer covers this hold",
        )

    balance_before = wallet.balance
    balance_after = balance_before - hold.amount
    metadata = {"wallet_hold_id": str(hold.id)}
    promo_used, purchased_used = await _consume_promo_grants_fifo(
        db, wallet, hold.amount
    )
    metadata["promo_bubbles"] = promo_used
    metadata["purchased_bubbles"] = purchased_used
    transaction = WalletTransaction(
        wallet_id=wallet.id,
        idempotency_key=f"wallet-hold-capture:{hold.id}",
        transaction_type=TransactionType.PURCHASE,
        direction=TransactionDirection.DEBIT,
        amount=hold.amount,
        balance_before=balance_before,
        balance_after=balance_after,
        status=TransactionStatus.COMPLETED,
        description=hold.description,
        service_source=hold.service_source,
        reference_type=hold.reference_type,
        reference_id=hold.reference_id,
        initiated_by=hold.service_source,
        txn_metadata=metadata,
    )
    db.add(transaction)
    await db.flush()

    now = utc_now()
    wallet.balance = balance_after
    wallet.lifetime_bubbles_spent += hold.amount
    wallet.updated_at = now
    hold.status = WalletHoldStatus.CAPTURED
    hold.captured_at = now
    hold.wallet_transaction_id = transaction.id
    await db.commit()
    await db.refresh(hold)

    from services.wallet_service.services.ledger_emit import emit_wallet_txn_to_ledger

    await emit_wallet_txn_to_ledger(db, transaction, hold.member_auth_id)
    available = await available_wallet_balance(db, wallet)
    logger.info("Captured wallet hold %s as transaction %s", hold.id, transaction.id)
    return hold, available


async def release_wallet_hold(
    db: AsyncSession, hold_id: uuid.UUID
) -> tuple[WalletHold, int]:
    hold = (
        await db.execute(
            select(WalletHold).where(WalletHold.id == hold_id).with_for_update()
        )
    ).scalar_one_or_none()
    if hold is None:
        raise HTTPException(status_code=404, detail="Wallet hold not found")
    wallet = (
        await db.execute(
            select(Wallet).where(Wallet.id == hold.wallet_id).with_for_update()
        )
    ).scalar_one()

    if hold.status == WalletHoldStatus.CAPTURED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A captured wallet hold must be refunded, not released",
        )
    if hold.status == WalletHoldStatus.HELD:
        hold.status = WalletHoldStatus.RELEASED
        hold.released_at = utc_now()
        await db.commit()
        await db.refresh(hold)
        logger.info("Released wallet hold %s", hold.id)

    return hold, await available_wallet_balance(db, wallet)
