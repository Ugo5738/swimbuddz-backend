"""Wallet → ledger emitter dead-letter test (durability parity with payments).

A ledger failure during a Bubbles movement must NOT affect the (committed) wallet
op, and must NOT silently drop the journal entry — it parks a
wallet_ledger_post_failures row for replay.
"""

import uuid
from datetime import datetime, timezone

from services.wallet_service.models import (
    TransactionType,
    WalletLedgerPostFailure,
    WalletTransaction,
)
from services.wallet_service.services import ledger_emit
from sqlalchemy import select


async def test_wallet_emit_dead_letters_on_ledger_failure(db_session, monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(ledger_emit, "post_journal_entry", _boom)

    # Transient REWARD txn — the emitter only reads attributes; not persisted.
    txn = WalletTransaction(
        id=uuid.uuid4(),
        transaction_type=TransactionType.REWARD,
        amount=10,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    # Must NOT raise.
    await ledger_emit.emit_wallet_txn_to_ledger(db_session, txn, "auth-xyz")

    key = f"wallet:{TransactionType.REWARD.value}:{txn.id}"
    row = (
        await db_session.execute(
            select(WalletLedgerPostFailure).where(
                WalletLedgerPostFailure.idempotency_key == key
            )
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.status == "pending"
    assert "ledger down" in (row.last_error or "")
    assert row.payload["source_id"] == str(txn.id)
