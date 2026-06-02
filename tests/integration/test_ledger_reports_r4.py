"""R4 report tests: balance sheet, margin, bubbles liability, cash position.

All post balanced entries within the rolled-back db_session (nothing persists)
and assert via deltas / a unique domain tag so they're robust to whatever the
dev DB already holds.
"""

import uuid
from datetime import date

from libs.common.config import get_settings
from services.ledger_service.schemas.journal import JournalEntryCreate
from services.ledger_service.services.posting import post_entry
from services.ledger_service.services.reports import (
    balance_sheet,
    bubbles_liability,
    cash_position,
    margin_by_domain,
)
from sqlalchemy import text

AS_OF = date(2026, 6, 30)
FROM_D = date(2026, 6, 1)
TO_D = date(2026, 6, 30)


async def _org_id(db_session) -> uuid.UUID:
    return uuid.UUID((get_settings().LEDGER_DEFAULT_ORG_ID or "").strip())


async def _ctx(db_session, org_id) -> None:
    await db_session.execute(
        text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_id)}
    )


async def _post(db_session, org_id, lines: list[dict]) -> None:
    await post_entry(
        db_session,
        org_id=org_id,
        payload=JournalEntryCreate(
            idempotency_key=f"r4-{uuid.uuid4().hex}",
            entry_date=date(2026, 6, 15),
            description="r4 report test",
            source_service="test",
            source_type="r4",
            source_id=uuid.uuid4().hex,
            lines=lines,
        ),
    )


async def test_balance_sheet_balances_with_earnings_row(db_session):
    org_id = await _org_id(db_session)
    await _ctx(db_session, org_id)

    # Cash-in to revenue: asset up, and net income (equity) up by the same.
    await _post(
        db_session,
        org_id,
        [
            {"account_ref": "paystack_clearing", "debit": 70_000, "currency": "NGN"},
            {"account_ref": "revenue_community", "credit": 70_000, "currency": "NGN"},
        ],
    )

    bs = await balance_sheet(db_session, org_id, AS_OF)
    assert bs.balanced is True
    assert bs.total_assets_minor == bs.total_liabilities_and_equity_minor
    # Revenue with no close → a synthetic current-year-earnings equity row exists.
    assert any("Current-Year Earnings" in r.name for r in bs.equity.rows)


async def test_margin_by_domain_revenue_minus_cogs(db_session):
    org_id = await _org_id(db_session)
    await _ctx(db_session, org_id)
    domain = f"td-{uuid.uuid4().hex[:8]}"

    # Revenue 100k + COGS 30k, both tagged to a unique domain so the row is ours.
    await _post(
        db_session,
        org_id,
        [
            {"account_ref": "paystack_clearing", "debit": 100_000, "currency": "NGN"},
            {
                "account_ref": "revenue_store",
                "credit": 100_000,
                "currency": "NGN",
                "dimension_1": domain,
            },
        ],
    )
    await _post(
        db_session,
        org_id,
        [
            {
                "account_ref": "cogs_store",
                "debit": 30_000,
                "currency": "NGN",
                "dimension_1": domain,
            },
            {"account_ref": "accounts_payable", "credit": 30_000, "currency": "NGN"},
        ],
    )

    report = await margin_by_domain(db_session, org_id, FROM_D, TO_D)
    row = next(r for r in report.rows if r.domain == domain)
    assert row.revenue_minor == 100_000
    assert row.cogs_minor == 30_000
    assert row.margin_minor == 70_000
    assert row.margin_pct == 70.0


async def test_bubbles_liability_split_tracks_promo_vs_purchased(db_session):
    org_id = await _org_id(db_session)
    await _ctx(db_session, org_id)

    before = await bubbles_liability(db_session, org_id, AS_OF)
    await _post(
        db_session,
        org_id,
        [
            {"account_ref": "bank_operating_ngn", "debit": 5_000, "currency": "NGN"},
            {"account_ref": "bubbles_liability", "credit": 5_000, "currency": "NGN"},
        ],
    )
    await _post(
        db_session,
        org_id,
        [
            {"account_ref": "expense_marketing", "debit": 2_000, "currency": "NGN"},
            {
                "account_ref": "bubbles_liability_promo",
                "credit": 2_000,
                "currency": "NGN",
            },
        ],
    )
    after = await bubbles_liability(db_session, org_id, AS_OF)

    assert after.purchased_minor - before.purchased_minor == 5_000
    assert after.promotional_minor - before.promotional_minor == 2_000
    assert after.total_minor == after.purchased_minor + after.promotional_minor


async def test_cash_position_splits_bank_and_clearing(db_session):
    org_id = await _org_id(db_session)
    await _ctx(db_session, org_id)

    before = await cash_position(db_session, org_id, AS_OF)
    # Settlement-shaped: clearing drains into bank.
    await _post(
        db_session,
        org_id,
        [
            {"account_ref": "bank_operating_ngn", "debit": 9_000, "currency": "NGN"},
            {"account_ref": "paystack_clearing", "credit": 9_000, "currency": "NGN"},
        ],
    )
    after = await cash_position(db_session, org_id, AS_OF)

    assert after.bank_minor - before.bank_minor == 9_000
    assert after.clearing_minor - before.clearing_minor == -9_000
    assert after.total_minor == after.bank_minor + after.clearing_minor
