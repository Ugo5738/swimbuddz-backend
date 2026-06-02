"""Tests for the Paystack settlement -> ledger drain emitter + ingest (R3-PR1).

The emitter line-building is pure (monkeypatch post_journal_entry to capture).
The dead-letter test asserts a failed post parks a ledger_post_failures row and
the emitter returns False. The ingest dry-run test asserts NO ledger post and a
correct would-drain total.
"""

import uuid
from datetime import date

from services.payments_service import tasks as tasks_mod
from services.payments_service.models import PaystackSettlement
from services.payments_service.models.ledger_failure import LedgerPostFailure
from services.payments_service.services import ledger_emit
from services.payments_service.services import paystack_client as pc_mod
from sqlalchemy import select

# asyncio_mode=auto (pytest.ini) auto-marks async tests.


def _settlement(gross=1_000_000, net=985_000, fees=15_000, sid=None):
    # Transient PaystackSettlement — the emitter only reads attributes.
    return PaystackSettlement(
        paystack_settlement_id=sid or f"STL-{uuid.uuid4().hex[:8]}",
        status="success",
        currency="NGN",
        gross_minor=gross,
        net_minor=net,
        fees_minor=fees,
        settlement_date=date(2026, 5, 1),
        raw_payload={},
    )


async def test_emit_settlement_balanced_and_correct_refs(db_session, monkeypatch):
    captured: dict = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return {"entry_id": "x", "status": "posted"}

    monkeypatch.setattr(ledger_emit, "post_journal_entry", _capture)

    ok = await ledger_emit.emit_settlement_to_ledger(db_session, _settlement())
    assert ok is True

    lines = captured["lines"]
    debit = sum(line.get("debit", 0) for line in lines)
    credit = sum(line.get("credit", 0) for line in lines)
    assert debit == credit == 1_000_000
    by_ref = {line["account_ref"]: line for line in lines}
    assert by_ref["bank_operating_ngn"]["debit"] == 985_000
    assert by_ref["expense_psp_fees"]["debit"] == 15_000
    assert by_ref["paystack_clearing"]["credit"] == 1_000_000
    assert captured["source_service"] == "payments"
    assert captured["source_type"] == "settlement"


async def test_emit_settlement_no_fee_line_when_net_equals_gross(
    db_session, monkeypatch
):
    captured: dict = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(ledger_emit, "post_journal_entry", _capture)
    await ledger_emit.emit_settlement_to_ledger(
        db_session, _settlement(gross=500_000, net=500_000, fees=0)
    )
    refs = {line["account_ref"] for line in captured["lines"]}
    assert refs == {"bank_operating_ngn", "paystack_clearing"}  # no fee line


async def test_emit_settlement_dead_letters_and_returns_false(db_session, monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(ledger_emit, "post_journal_entry", _boom)

    s = _settlement(sid="STL-DEADLETTER-TEST")
    ok = await ledger_emit.emit_settlement_to_ledger(db_session, s)
    assert ok is False

    key = "payments:settlement:STL-DEADLETTER-TEST"
    row = (
        await db_session.execute(
            select(LedgerPostFailure).where(LedgerPostFailure.idempotency_key == key)
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.status == "pending"
    assert "ledger down" in (row.last_error or "")


async def test_ingest_dry_run_does_not_post_and_totals_gross(monkeypatch):
    sid1 = f"S-{uuid.uuid4().hex[:8]}"
    sid2 = f"S-{uuid.uuid4().hex[:8]}"

    class _FakeClient:
        async def list_settlements(self, **kwargs):
            return [
                # Real Paystack shape: effective_amount == total_amount (the NET
                # settled to bank); total_fees charged on top. Gross = net + fees.
                {
                    "id": sid1,
                    "total_amount": 100_000,
                    "effective_amount": 100_000,
                    "total_fees": 1_500,
                    "status": "success",
                    "currency": "NGN",
                    "settlement_date": "2026-05-01T00:00:00.000Z",
                },
                {
                    "id": sid2,
                    "total_amount": 200_000,
                    "effective_amount": 200_000,
                    "total_fees": 3_000,
                    "status": "success",
                    "currency": "NGN",
                    "settlement_date": "2026-05-02T00:00:00.000Z",
                },
            ]

    monkeypatch.setattr(pc_mod, "get_paystack_client", lambda: _FakeClient())

    emit_calls: list = []

    async def _fake_emit(db, s):
        emit_calls.append(s)
        return True

    monkeypatch.setattr(ledger_emit, "emit_settlement_to_ledger", _fake_emit)

    summary = await tasks_mod.ingest_paystack_settlements(
        lookback_days=30, commit=False
    )
    assert summary["fetched"] == 2
    # gross = net + fees: (100_000 + 1_500) + (200_000 + 3_000)
    assert summary["would_drain_minor"] == 304_500
    assert emit_calls == []  # dry-run must NEVER post to the ledger
