"""The reviewed repair runs only against an explicitly isolated local PG."""

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from libs.db.base import Base
from scripts.ops import reallocate_amara_bubbles_20261116 as op
from services.payments_service.models import Payment
from services.payments_service.services.booking_payment_attempts import (
    blocks_new_attempt,
)
from services.sessions_service.models import SessionBooking  # noqa: F401
from services.wallet_service.models import Wallet, WalletTransaction  # noqa: F401


@pytest_asyncio.fixture
async def case():
    url = os.environ.get("AMARA_CORRECTION_TEST_URL")
    if not url:
        pytest.skip("Set AMARA_CORRECTION_TEST_URL to isolated local PostgreSQL")
    assert make_url(url).host in {"localhost", "127.0.0.1"}
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        outer = await conn.begin()
        schema = "amara_test_" + uuid.uuid4().hex
        await conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
        await conn.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
        await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as db:
            db.add(
                Wallet(
                    id=uuid.UUID(op.WALLET),
                    member_id=uuid.UUID(op.MEMBER),
                    member_auth_id=op.AUTH,
                    balance=56,
                    lifetime_bubbles_received=100,
                    lifetime_bubbles_spent=52,
                )
            )
            await db.flush()
            db.add(
                WalletTransaction(
                    id=uuid.UUID(op.OLD_TXN),
                    wallet_id=uuid.UUID(op.WALLET),
                    idempotency_key="original-purchase",
                    transaction_type="purchase",
                    direction="debit",
                    amount=52,
                    balance_before=108,
                    balance_after=56,
                    status="completed",
                    description="Original Yaba purchase",
                    service_source="payments",
                    reference_type="session_booking",
                    reference_id=op.OLD_PAYMENT,
                    txn_metadata={"promo_bubbles": 0, "purchased_bubbles": 52},
                    created_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
                )
            )
            db.add(
                WalletTransaction(
                    id=uuid.UUID(op.AWARD_TXN),
                    wallet_id=uuid.UUID(op.WALLET),
                    idempotency_key="original-award",
                    transaction_type="admin_adjustment",
                    direction="credit",
                    amount=100,
                    balance_before=8,
                    balance_after=108,
                    status="completed",
                    description="Original award",
                    txn_metadata={
                        "reason": "Complimentary Oct 3 Community Swim — Volunteer of the Month"
                    },
                    created_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
                )
            )
            db.add(
                Payment(
                    id=uuid.UUID(op.OLD_PAYMENT),
                    reference="PAY-08475",
                    member_auth_id=op.AUTH,
                    purpose="session_booking",
                    amount=0,
                    currency="NGN",
                    status="paid",
                    payment_metadata={
                        "booking_id": op.OLD_BOOKING,
                        "wallet_transaction_id": op.OLD_TXN,
                        "bubbles_to_apply": 52,
                        "server_price": {"total_kobo": 520000},
                    },
                    entitlement_applied_at=datetime(2026, 10, 30, tzinfo=timezone.utc),
                )
            )
            for bid, sid, amount in [
                (op.OLD_BOOKING, "9c45145e-25c3-54a2-b886-18aa29489d8a", 520000),
                (op.COMMUNITY_BOOKING, "00e64c99-1fbe-45fc-82f7-88c5898d4bc1", 1000000),
            ]:
                db.add(
                    SessionBooking(
                        id=uuid.UUID(bid),
                        session_id=uuid.UUID(sid),
                        member_id=uuid.UUID(op.MEMBER),
                        member_auth_id=op.AUTH,
                        status="confirmed",
                        channel="admin",
                        party_size=1,
                        fee_amount_kobo=amount,
                        member_fee_amount_kobo=amount,
                        payment_intent_id=uuid.UUID(op.OLD_PAYMENT)
                        if bid == op.OLD_BOOKING
                        else None,
                        wallet_transaction_id=uuid.UUID(op.OLD_TXN)
                        if bid == op.OLD_BOOKING
                        else None,
                    )
                )
            await db.flush()
            await db.commit()
            yield db
        await outer.rollback()
    await engine.dispose()


async def test_dry_run_changes_nothing(case):
    result = await op.repair(case)
    assert result["wallet_balance_after"] == 8
    assert (await op.rows(case, "SELECT balance FROM wallets"))[0]["balance"] == 56
    assert len(await op.rows(case, "SELECT id FROM wallet_transactions")) == 2


async def test_apply_is_atomic_idempotent_and_retains_original_receipt(case):
    result = await op.repair(case, apply=True, actor_label="test-owner-authorized")
    await case.commit()
    replay = await op.repair(case, apply=True, actor_label="test-owner-authorized")
    assert replay["already_applied"]
    assert result["wallet_balance_after"] == 8
    assert (await op.rows(case, "SELECT balance FROM wallets"))[0]["balance"] == 8
    txns = await op.rows(case, "SELECT * FROM wallet_transactions")
    assert len(txns) == 4
    assert (
        sum(t["amount"] if t["direction"] == "credit" else -t["amount"] for t in txns)
        == 0
    )  # seed balance 8 retained
    old = (
        await op.rows(
            case,
            "SELECT * FROM session_bookings WHERE id=:id",
            id=uuid.UUID(op.OLD_BOOKING),
        )
    )[0]
    assert old["payment_intent_id"] is None and old["wallet_transaction_id"] is None
    assert old["fee_amount_kobo"] == 520000 and old["status"] == "confirmed"
    payment = await case.get(Payment, uuid.UUID(op.OLD_PAYMENT), populate_existing=True)
    assert payment.status.value == "paid" and payment.amount == 0
    assert not blocks_new_attempt(payment)
    assert payment.entitlement_applied_at
    payloads = await op.rows(case, "SELECT payload FROM wallet_ledger_post_failures")
    assert len(payloads) == 4
    totals = {}
    for row in payloads:
        lines = row["payload"]["lines"]
        assert sum(line["debit"] for line in lines) == sum(
            line["credit"] for line in lines
        )
        for line in lines:
            totals[line["account_ref"]] = (
                totals.get(line["account_ref"], 0) + line["credit"] - line["debit"]
            )
    assert totals["revenue_club_session"] == 0
    assert totals["revenue_events"] == 1000000
    assert totals["expense_marketing"] == -1000000
    assert not any("bank" in account or "clearing" in account for account in totals)


@pytest.mark.parametrize(
    "change",
    ["cash", "new_payment", "changed_balance", "wrong_award", "already_reversed"],
)
async def test_changed_evidence_aborts_without_wallet_movement(case, change):
    sql = {
        "cash": "UPDATE payments SET amount=100",
        "new_payment": f"UPDATE session_bookings SET payment_intent_id='{uuid.uuid4()}' WHERE id='{op.COMMUNITY_BOOKING}'",
        "changed_balance": "UPDATE wallets SET balance=57",
        "wrong_award": f"UPDATE wallet_transactions SET txn_metadata='{{}}'::jsonb WHERE id='{op.AWARD_TXN}'",
        "already_reversed": f"UPDATE wallet_transactions SET reversed_by_transaction_id='{uuid.uuid4()}' WHERE id='{op.OLD_TXN}'",
    }[change]
    await case.execute(text(sql))
    await case.flush()
    with pytest.raises(ValueError):
        await op.repair(case, apply=True, actor_label="test")
    assert len(await op.rows(case, "SELECT id FROM wallet_transactions")) == 2
    assert not await op.rows(case, "SELECT id FROM wallet_audit_logs")


async def test_failure_rolls_back_all_financial_changes(case, monkeypatch):
    original = case.execute

    async def fail_audit(statement, *args, **kwargs):
        if "INSERT INTO wallet_audit_logs" in str(statement):
            raise RuntimeError("Simulated audit failure")
        return await original(statement, *args, **kwargs)

    monkeypatch.setattr(case, "execute", fail_audit)
    with pytest.raises(RuntimeError):
        await op.repair(case, apply=True, actor_label="test")
    await case.rollback()
    assert (await op.rows(case, "SELECT balance FROM wallets"))[0]["balance"] == 56
    assert len(await op.rows(case, "SELECT id FROM wallet_transactions")) == 2


async def test_ledger_replay_is_scoped_and_idempotent(case, monkeypatch):
    from unittest.mock import AsyncMock
    from libs.common import ledger_client

    await op.repair(case, apply=True, actor_label="test")
    await case.commit()
    post = AsyncMock(return_value={"entry_id": str(uuid.uuid4()), "status": "posted"})
    monkeypatch.setattr(ledger_client, "post_journal_entry", post)
    assert len(await op.replay_ledger(case)) == 4
    await op.replay_ledger(case)
    assert post.await_count == 4
    assert all(
        row["status"] == "replayed"
        for row in await op.rows(case, "SELECT status FROM wallet_ledger_post_failures")
    )


async def test_failed_journal_post_retains_durable_pending_payload(case, monkeypatch):
    from unittest.mock import AsyncMock
    from libs.common import ledger_client

    await op.repair(case, apply=True, actor_label="test")
    await case.commit()
    monkeypatch.setattr(
        ledger_client,
        "post_journal_entry",
        AsyncMock(side_effect=RuntimeError("ledger unavailable")),
    )
    with pytest.raises(RuntimeError):
        await op.replay_ledger(case)
    assert all(
        row["status"] == "pending"
        for row in await op.rows(case, "SELECT status FROM wallet_ledger_post_failures")
    )
    assert (await op.rows(case, "SELECT balance FROM wallets"))[0]["balance"] == 8
