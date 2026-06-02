"""Integration tests for settlement reconciliation (R3-PR2).

Ledger side: post journal entries with external_ref, then intake external
transactions and assert match outcomes (matched / unmatched / amount_mismatch)
+ breaks. Payments side: the txn->intake mapping is pure (mock the client +
push). All ledger work runs in the rolled-back db_session — nothing persists.
"""

import uuid
from datetime import date

from libs.common.config import get_settings
from services.ledger_service.models import ExternalTransaction
from services.ledger_service.schemas.journal import JournalEntryCreate
from services.ledger_service.schemas.reconciliation import ExternalTransactionIn
from services.ledger_service.services.posting import post_entry
from services.ledger_service.services.reconciliation import (
    intake_external_transactions,
    reconciliation_report,
)
from sqlalchemy import select, text


async def _org_id(db_session) -> uuid.UUID:
    return uuid.UUID((get_settings().LEDGER_DEFAULT_ORG_ID or "").strip())


async def _set_ctx(db_session, org_id) -> None:
    await db_session.execute(
        text("SELECT set_config('app.current_org_id', :o, true)"),
        {"o": str(org_id)},
    )


async def _post_cashin(db_session, org_id, ref: str, amount: int) -> None:
    """Post a cash-in-shaped entry carrying external_ref on both lines."""
    await post_entry(
        db_session,
        org_id=org_id,
        payload=JournalEntryCreate(
            idempotency_key=f"recon-{ref}",
            entry_date=date(2026, 6, 1),
            description="recon test cash-in",
            source_service="payments",
            source_type="payment_paid",
            source_id=ref,
            lines=[
                {
                    "account_ref": "paystack_clearing",
                    "debit": amount,
                    "currency": "NGN",
                    "external_ref": ref,
                },
                {
                    "account_ref": "deferred_revenue_academy",
                    "credit": amount,
                    "currency": "NGN",
                    "external_ref": ref,
                },
            ],
        ),
    )


def _ext(ref, amount, *, txn_id=None) -> ExternalTransactionIn:
    return ExternalTransactionIn(
        psp="paystack",
        external_txn_id=txn_id or f"T-{uuid.uuid4().hex[:10]}",
        external_ref=ref,
        settlement_ref="SET-RECON-1",
        amount_minor=amount,
        fee_minor=1500,
        currency="NGN",
        status="success",
    )


async def test_intake_matches_unmatched_and_mismatch(db_session):
    org_id = await _org_id(db_session)
    await _set_ctx(db_session, org_id)

    suffix = uuid.uuid4().hex[:6]
    ref_match = f"RECON-MATCH-{suffix}"
    ref_mismatch = f"RECON-MISMATCH-{suffix}"
    ref_missing = f"RECON-MISSING-{suffix}"

    await _post_cashin(db_session, org_id, ref_match, 100_000)
    await _post_cashin(db_session, org_id, ref_mismatch, 80_000)

    summary = await intake_external_transactions(
        db_session,
        org_id,
        [
            _ext(ref_match, 100_000),  # ties out -> matched
            _ext(ref_missing, 50_000),  # no entry -> unmatched break
            _ext(ref_mismatch, 79_999),  # entry exists, amount differs -> break
        ],
    )

    assert summary["received"] == 3
    assert summary["matched"] == 1
    assert summary["breaks_opened"] == 2

    rows = {
        r.external_ref: r
        for r in (
            await db_session.execute(
                select(ExternalTransaction).where(
                    ExternalTransaction.org_id == org_id,
                    ExternalTransaction.external_ref.in_(
                        [ref_match, ref_missing, ref_mismatch]
                    ),
                )
            )
        )
        .scalars()
        .all()
    }
    assert rows[ref_match].match_status == "matched"
    assert rows[ref_match].matched_entry_id is not None
    assert rows[ref_missing].match_status == "unmatched"
    assert rows[ref_mismatch].match_status == "amount_mismatch"

    report = await reconciliation_report(db_session, org_id, limit=500)
    open_refs = {b.external_ref for b in report.breaks}
    assert ref_missing in open_refs
    assert ref_mismatch in open_refs
    assert ref_match not in open_refs


async def test_intake_is_idempotent_and_resolves_on_rematch(db_session):
    org_id = await _org_id(db_session)
    await _set_ctx(db_session, org_id)

    suffix = uuid.uuid4().hex[:6]
    ref = f"RECON-LATE-{suffix}"
    txn_id = f"T-LATE-{suffix}"

    # First sweep: settled but not yet booked -> unmatched break.
    s1 = await intake_external_transactions(
        db_session, org_id, [_ext(ref, 60_000, txn_id=txn_id)]
    )
    assert s1["inserted"] == 1 and s1["breaks_opened"] == 1

    # The entry posts later, then the same txn is re-pushed (same txn id).
    await _post_cashin(db_session, org_id, ref, 60_000)
    s2 = await intake_external_transactions(
        db_session, org_id, [_ext(ref, 60_000, txn_id=txn_id)]
    )
    assert s2["inserted"] == 0  # idempotent upsert, not a new row
    assert s2["matched"] == 1

    report = await reconciliation_report(db_session, org_id, limit=500)
    assert ref not in {b.external_ref for b in report.breaks}  # break auto-resolved


async def test_payments_reconcile_maps_and_pushes(monkeypatch):
    import libs.common.ledger_client as lc
    from services.payments_service import tasks as tasks_mod

    captured: dict = {}

    async def _fake_post(**kwargs):
        captured.update(kwargs)
        return {"received": 1, "inserted": 1, "matched": 1, "breaks_opened": 0}

    monkeypatch.setattr(lc, "post_external_transactions", _fake_post)

    class _Client:
        async def list_settlement_transactions(self, sid):
            return [
                {
                    "id": 999,
                    "reference": "PAY-ABC123",
                    "amount": 100_000,
                    "fees": 1_500,
                    "currency": "NGN",
                    "status": "success",
                    "paid_at": "2026-05-01T10:00:00.000Z",
                }
            ]

    class _Settlement:
        paystack_settlement_id = "SET-1"

    pushed = await tasks_mod._reconcile_settlement_txns(_Client(), _Settlement())
    assert pushed is True
    items = captured["transactions"]
    assert len(items) == 1
    assert items[0]["external_ref"] == "PAY-ABC123"
    assert items[0]["external_txn_id"] == "999"
    assert items[0]["amount_minor"] == 100_000
    assert items[0]["fee_minor"] == 1_500
    assert items[0]["psp"] == "paystack"
    assert items[0]["settlement_ref"] == "SET-1"
