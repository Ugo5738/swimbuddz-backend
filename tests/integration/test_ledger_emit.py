"""Tests for the payments -> ledger emitter (P2-c / P2-d).

Mapping/kobo checks are pure (no DB). The dead-letter test uses db_session and
monkeypatches post_journal_entry to fail, asserting the payment path parks a
ledger_post_failures row instead of raising.
"""

import uuid

import pytest
from services.payments_service.models import Payment, PaymentPurpose
from services.payments_service.models.ledger_failure import LedgerPostFailure
from services.payments_service.services import ledger_emit
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


def _payment(
    purpose: PaymentPurpose = PaymentPurpose.ACADEMY_COHORT, amount: float = 150000.0
) -> Payment:
    # Transient Payment (not persisted) — the emitter only reads attributes.
    return Payment(
        reference=f"PAY-{uuid.uuid4().hex[:8]}",
        member_auth_id="auth-123",
        purpose=purpose,
        amount=amount,
        currency="NGN",
        provider="paystack",
    )


def test_build_post_kwargs_academy_is_balanced_and_correct_refs():
    kwargs = ledger_emit.build_post_kwargs(_payment())
    assert kwargs is not None
    debit = sum(line.get("debit", 0) for line in kwargs["lines"])
    credit = sum(line.get("credit", 0) for line in kwargs["lines"])
    assert debit == credit == 15_000_000
    refs = {line["account_ref"] for line in kwargs["lines"]}
    assert refs == {"paystack_clearing", "deferred_revenue_academy"}
    assert kwargs["source_service"] == "payments"
    assert kwargs["source_type"] == "payment_paid"


def test_build_post_kwargs_wallet_topup_credits_bubbles_liability():
    kwargs = ledger_emit.build_post_kwargs(
        _payment(PaymentPurpose.WALLET_TOPUP, amount=5000.0)
    )
    credit_line = next(line for line in kwargs["lines"] if line.get("credit"))
    assert credit_line["account_ref"] == "bubbles_liability"
    assert credit_line["credit"] == 500_000


async def test_emit_dead_letters_on_ledger_failure(db_session, monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(ledger_emit, "post_journal_entry", _boom)

    payment = _payment()
    # Must NOT raise — a ledger failure can't affect the payment.
    await ledger_emit.emit_payment_to_ledger(db_session, payment)

    key = f"payments:payment_paid:{payment.reference}"
    row = (
        await db_session.execute(
            select(LedgerPostFailure).where(LedgerPostFailure.idempotency_key == key)
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.status == "pending"
    assert "ledger down" in (row.last_error or "")
    assert row.payload["source_id"] == payment.reference
