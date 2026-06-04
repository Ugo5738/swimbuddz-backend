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


async def test_wallet_emit_values_bubble_at_100_naira(db_session, monkeypatch):
    """1 Bubble = ₦100 = 10,000 kobo (guards the NAIRA/KOBO_PER_BUBBLE regression)."""
    captured: dict = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return {"entry_id": "x", "status": "posted"}

    monkeypatch.setattr(ledger_emit, "post_journal_entry", _capture)

    txn = WalletTransaction(
        id=uuid.uuid4(),
        transaction_type=TransactionType.REWARD,
        amount=10,  # 10 Bubbles -> ₦1,000 -> 100,000 kobo
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    await ledger_emit.emit_wallet_txn_to_ledger(db_session, txn, "auth-xyz")

    lines = captured["lines"]
    debit = sum(line.get("debit", 0) for line in lines)
    credit = sum(line.get("credit", 0) for line in lines)
    assert debit == credit == 100_000  # NOT 1,000 (the ₦1/Bubble bug)


def _capture(monkeypatch):
    captured: dict = {}

    async def _cap(**kw):
        captured.update(kw)
        return {"entry_id": "x", "status": "posted"}

    monkeypatch.setattr(ledger_emit, "post_journal_entry", _cap)
    return captured


def _session_txn(kind):
    return WalletTransaction(
        id=uuid.uuid4(),
        transaction_type=kind,
        amount=5,  # 5 Bubbles = ₦500 = 50,000 kobo
        service_source="sessions",
        reference_type="session_booking",
        reference_id="sess-1",
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )


async def test_session_booking_spend_posts_club_revenue(db_session, monkeypatch):
    captured = _capture(monkeypatch)
    await ledger_emit.emit_wallet_txn_to_ledger(
        db_session, _session_txn(TransactionType.PURCHASE), "auth-1"
    )
    by = {line["account_ref"]: line for line in captured["lines"]}
    assert by["bubbles_liability"]["debit"] == 50_000  # Bubbles spent
    assert by["revenue_club_session"]["credit"] == 50_000  # revenue recognised


async def test_session_refund_reverses_club_revenue(db_session, monkeypatch):
    captured = _capture(monkeypatch)
    await ledger_emit.emit_wallet_txn_to_ledger(
        db_session, _session_txn(TransactionType.REFUND), "auth-1"
    )
    by = {line["account_ref"]: line for line in captured["lines"]}
    # Cancelled session refunded in Bubbles: un-earn revenue, restore the liability.
    assert by["revenue_club_session"]["debit"] == 50_000
    assert by["bubbles_liability"]["credit"] == 50_000
    assert "refunds_payable" not in by
