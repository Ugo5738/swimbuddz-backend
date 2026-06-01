"""Integration tests for the ledger posting engine (P2-d).

Run in-container against the dev DB (the SwimBuddz org + CoA must be seeded):
    docker compose exec ledger-service pytest tests/integration/test_ledger_posting.py -v

These call the posting service directly within the rolled-back db_session
transaction, so nothing persists. They exercise: balanced post + balance
recompute, idempotent replay, and unknown-account rejection.
"""

import uuid
from datetime import date

import pytest
from libs.common.config import get_settings
from services.ledger_service.models import (
    AccountBalance,
    JournalEntry,
    JournalLine,
    Organization,
)
from services.ledger_service.schemas.journal import JournalEntryCreate
from services.ledger_service.services.accounts import resolve_account_ids
from services.ledger_service.services.posting import (
    UnresolvedAccountError,
    post_entry,
)
from sqlalchemy import func, select, text

pytestmark = pytest.mark.asyncio


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
        text("SELECT set_config('app.current_org_id', :o, true)"),
        {"o": str(org_id)},
    )


def _balanced(
    key: str, debit_ref: str, credit_ref: str, amount: int
) -> JournalEntryCreate:
    return JournalEntryCreate(
        idempotency_key=key,
        entry_date=date(2026, 6, 1),
        description="posting test",
        source_service="test",
        source_type="unit",
        source_id=key,
        lines=[
            {"account_ref": debit_ref, "debit": amount, "currency": "NGN"},
            {"account_ref": credit_ref, "credit": amount, "currency": "NGN"},
        ],
    )


async def test_balanced_post_creates_entry_and_balances(db_session):
    org_id = await _org_id(db_session)
    await _set_ctx(db_session, org_id)
    key = f"test:{uuid.uuid4()}"

    res = await post_entry(
        db_session,
        org_id=org_id,
        payload=_balanced(
            key, "paystack_clearing", "deferred_revenue_academy", 15_000_000
        ),
    )
    assert res.idempotent_replay is False

    lines = (
        (
            await db_session.execute(
                select(JournalLine).where(JournalLine.entry_id == res.entry_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(lines) == 2
    assert sum(line.debit_minor for line in lines) == 15_000_000
    assert sum(line.credit_minor for line in lines) == 15_000_000

    accts = await resolve_account_ids(
        db_session, org_id, ["paystack_clearing", "deferred_revenue_academy"]
    )
    bals = (
        (
            await db_session.execute(
                select(AccountBalance).where(
                    AccountBalance.period_id == res.period_id,
                    AccountBalance.account_id.in_(list(accts.values())),
                )
            )
        )
        .scalars()
        .all()
    )
    closing = {b.account_id: b.closing_minor for b in bals}
    # debit-positive convention; the two sides net to zero
    assert closing[accts["paystack_clearing"]] == 15_000_000
    assert closing[accts["deferred_revenue_academy"]] == -15_000_000
    assert sum(b.closing_minor for b in bals) == 0


async def test_idempotent_replay_returns_same_entry(db_session):
    org_id = await _org_id(db_session)
    await _set_ctx(db_session, org_id)
    key = f"test:{uuid.uuid4()}"

    r1 = await post_entry(
        db_session,
        org_id=org_id,
        payload=_balanced(key, "paystack_clearing", "revenue_store", 500_000),
    )
    r2 = await post_entry(
        db_session,
        org_id=org_id,
        payload=_balanced(key, "paystack_clearing", "revenue_store", 500_000),
    )
    assert r2.idempotent_replay is True
    assert r1.entry_id == r2.entry_id

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(JournalEntry)
            .where(
                JournalEntry.org_id == org_id,
                JournalEntry.idempotency_key == key,
            )
        )
    ).scalar()
    assert count == 1


async def test_unknown_account_ref_rejected(db_session):
    org_id = await _org_id(db_session)
    await _set_ctx(db_session, org_id)
    key = f"test:{uuid.uuid4()}"
    with pytest.raises(UnresolvedAccountError):
        await post_entry(
            db_session,
            org_id=org_id,
            payload=_balanced(key, "paystack_clearing", "nonexistent_ref_xyz", 1_000),
        )


async def test_unbalanced_entry_rejected_at_schema():
    # The schema is the first gate — unbalanced entries can't even be constructed.
    with pytest.raises(ValueError):
        _balanced  # noqa: B018
        JournalEntryCreate(
            idempotency_key="k",
            entry_date=date(2026, 6, 1),
            description="bad",
            source_service="test",
            source_type="unit",
            lines=[
                {"account_ref": "paystack_clearing", "debit": 100, "currency": "NGN"},
                {"account_ref": "revenue_store", "credit": 50, "currency": "NGN"},
            ],
        )
