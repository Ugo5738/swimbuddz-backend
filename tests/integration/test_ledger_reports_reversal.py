"""Integration tests for reversal + reports + role-guard (P3-d).

Service-level tests within the rolled-back db_session (nothing persists; uses the
seeded SwimBuddz org + CoA). Run in-container:
    docker compose exec ledger-service pytest tests/integration/test_ledger_reports_reversal.py -v
"""

import uuid
from datetime import date

import pytest
from fastapi import HTTPException
from libs.common.config import get_settings
from services.ledger_service.models import AccountBalance, JournalEntry, Organization
from services.ledger_service.models.enums import LedgerRole
from services.ledger_service.schemas.journal import JournalEntryCreate
from services.ledger_service.services.accounts import resolve_account_ids
from services.ledger_service.services.posting import post_entry, reverse_entry
from services.ledger_service.services.reports import profit_loss, trial_balance
from sqlalchemy import select, text

# asyncio_mode=auto (pytest.ini) auto-marks async tests; no module-level mark so
# the one sync test (guard-rank) isn't falsely tagged asyncio.


async def _org_id(db_session) -> uuid.UUID:
    configured = (get_settings().LEDGER_DEFAULT_ORG_ID or "").strip()
    if configured:
        return uuid.UUID(configured)
    org = (
        await db_session.execute(
            select(Organization).where(Organization.name == "SwimBuddz")
        )
    ).scalar_one()
    return org.id


async def _set_ctx(db_session, org_id: uuid.UUID) -> None:
    await db_session.execute(
        text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_id)}
    )


def _balanced(debit_ref, credit_ref, amount, *, dim1=None, entry_date=date(2026, 6, 1)):
    key = f"test:{uuid.uuid4()}"
    credit_line = {"account_ref": credit_ref, "credit": amount, "currency": "NGN"}
    if dim1:
        credit_line["dimension_1"] = dim1
    return JournalEntryCreate(
        idempotency_key=key,
        entry_date=entry_date,
        description="report/reversal test",
        source_service="test",
        source_type="unit",
        source_id=key,
        lines=[
            {"account_ref": debit_ref, "debit": amount, "currency": "NGN"},
            credit_line,
        ],
    )


async def test_reversal_nets_to_zero(db_session):
    org_id = await _org_id(db_session)
    await _set_ctx(db_session, org_id)

    res = await post_entry(
        db_session,
        org_id=org_id,
        payload=_balanced("paystack_clearing", "revenue_store", 800_000),
    )
    rev = await reverse_entry(
        db_session, org_id=org_id, entry_id=res.entry_id, reason="oops"
    )
    assert rev.idempotent_replay is False
    assert rev.entry_id != res.entry_id

    original = await db_session.get(JournalEntry, res.entry_id)
    assert original.status.value == "reversed"
    assert original.reversed_by_entry_id == rev.entry_id

    # Across all periods, the original + reversal net to zero for both accounts.
    accts = await resolve_account_ids(
        db_session, org_id, ["paystack_clearing", "revenue_store"]
    )
    bals = (
        (
            await db_session.execute(
                select(AccountBalance).where(
                    AccountBalance.org_id == org_id,
                    AccountBalance.account_id.in_(list(accts.values())),
                )
            )
        )
        .scalars()
        .all()
    )
    assert sum(b.closing_minor for b in bals) == 0


async def test_reverse_twice_is_blocked(db_session):
    org_id = await _org_id(db_session)
    await _set_ctx(db_session, org_id)
    res = await post_entry(
        db_session,
        org_id=org_id,
        payload=_balanced("paystack_clearing", "revenue_store", 100_000),
    )
    await reverse_entry(db_session, org_id=org_id, entry_id=res.entry_id)
    from services.ledger_service.services.posting import AlreadyReversedError

    with pytest.raises(AlreadyReversedError):
        await reverse_entry(db_session, org_id=org_id, entry_id=res.entry_id)


async def test_trial_balance_balances(db_session):
    org_id = await _org_id(db_session)
    await _set_ctx(db_session, org_id)
    await post_entry(
        db_session,
        org_id=org_id,
        payload=_balanced("paystack_clearing", "deferred_revenue_academy", 15_000_000),
    )
    await post_entry(
        db_session,
        org_id=org_id,
        payload=_balanced("paystack_clearing", "revenue_store", 800_000),
    )
    tb = await trial_balance(db_session, org_id, date(2026, 6, 30))
    assert tb.balanced is True
    assert tb.total_debit_minor == tb.total_credit_minor == 15_800_000


async def test_profit_loss_by_domain(db_session):
    org_id = await _org_id(db_session)
    await _set_ctx(db_session, org_id)
    await post_entry(
        db_session,
        org_id=org_id,
        payload=_balanced("paystack_clearing", "revenue_store", 800_000, dim1="store"),
    )
    pl = await profit_loss(
        db_session, org_id, date(2026, 6, 1), date(2026, 6, 30), group_by="dimension_1"
    )
    assert pl.total_revenue_minor == 800_000
    assert pl.total_expense_minor == 0
    assert pl.net_income_minor == 800_000
    store_row = next((r for r in pl.rows if r.key == "store"), None)
    assert store_row is not None and store_row.revenue_minor == 800_000


def test_guard_rank_blocks_privilege_escalation():
    from services.ledger_service.models import LedgerUser
    from services.ledger_service.routers.users import _guard_rank

    admin = LedgerUser(role=LedgerRole.ADMIN)
    _guard_rank(admin, LedgerRole.ACCOUNTANT)  # ok: lower
    _guard_rank(admin, LedgerRole.ADMIN)  # ok: equal
    with pytest.raises(HTTPException):
        _guard_rank(admin, LedgerRole.OWNER)  # blocked: higher
