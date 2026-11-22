import uuid
from datetime import date
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from services.wallet_service.models import (
    MemberRewardHistory,
    PromotionalBubbleGrant,
    Wallet,
)
from services.wallet_service.services import swim_rewards


@pytest.mark.asyncio
async def test_duplicate_days_correction_and_wallet_history(db_session, monkeypatch):
    member_id = uuid.uuid4()
    auth_id = str(uuid.uuid4())
    journal = AsyncMock()
    monkeypatch.setattr(swim_rewards, "post_journal_entry", journal)

    async def deliver(day, session_id=None, revision=1):
        session_id = session_id or uuid.uuid4()
        body = swim_rewards.SwimVerification(
            member_id=member_id,
            member_auth_id=auth_id,
            session_id=session_id,
            revision=revision,
            swim_date=date(2026, 10, day) if day else None,
        )
        await swim_rewards.reconcile_swim(db_session, body)
        return session_id

    first = await deliver(5)
    await deliver(5, first)
    await deliver(5)
    second = await deliver(6)
    await deliver(None, second, 2)
    await deliver(6, second, 1)
    summary = await swim_rewards.member_progress(db_session, auth_id)
    assert (summary.total_swims, summary.bubbles_earned, summary.bubbles_credited) == (
        2,
        1,
        1,
    )
    wallet_after_reversal = (
        await db_session.scalars(select(Wallet).where(Wallet.member_auth_id == auth_id))
    ).one()
    assert wallet_after_reversal.balance == 1
    await deliver(7)
    await deliver(8)
    await deliver(9)
    await deliver(12)
    summary = await swim_rewards.member_progress(db_session, auth_id)
    wallet = (
        await db_session.scalars(select(Wallet).where(Wallet.member_auth_id == auth_id))
    ).one()
    assert (wallet.balance, summary.bubbles_earned, summary.bubbles_credited) == (
        4,
        4,
        4,
    )
    assert (
        await db_session.scalar(
            select(func.sum(PromotionalBubbleGrant.bubbles_remaining)).where(
                PromotionalBubbleGrant.wallet_id == wallet.id
            )
        )
        == 4
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(MemberRewardHistory)
            .where(MemberRewardHistory.member_auth_id == auth_id)
        )
        == 5
    )
    assert journal.await_count == 6
    assert (
        journal.await_args.kwargs["lines"][1]["account_ref"]
        == "bubbles_liability_promo"
    )


@pytest.mark.asyncio
async def test_concurrent_verifications_respect_wallet_cap(test_engine, monkeypatch):
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession

    monkeypatch.setattr(swim_rewards, "post_journal_entry", AsyncMock())
    member_id = uuid.uuid4()
    auth_id = str(uuid.uuid4())

    async def deliver(day):
        async with AsyncSession(test_engine, expire_on_commit=False) as session:
            await swim_rewards.reconcile_swim(
                session,
                swim_rewards.SwimVerification(
                    member_id=member_id,
                    member_auth_id=auth_id,
                    session_id=uuid.uuid4(),
                    revision=1,
                    swim_date=date(2026, 10, day),
                ),
            )

    await asyncio.gather(*(deliver(day) for day in [5, 5, 6, 7, 8, 9]))
    async with AsyncSession(test_engine) as session:
        summary = await swim_rewards.member_progress(session, auth_id)
        balance = await session.scalar(
            select(Wallet.balance).where(Wallet.member_auth_id == auth_id)
        )
    assert (
        summary.total_swims,
        summary.bubbles_earned,
        summary.bubbles_credited,
        balance,
    ) == (6, 3, 3, 3)


@pytest.mark.asyncio
async def test_spent_swim_bubbles_offset_future_rewards_without_negative_balance(
    db_session, monkeypatch
):
    from services.wallet_service.models import TransactionType
    from services.wallet_service.services import ledger_emit
    from services.wallet_service.services.wallet_ops import debit_wallet

    monkeypatch.setattr(swim_rewards, "post_journal_entry", AsyncMock())
    monkeypatch.setattr(ledger_emit, "post_journal_entry", AsyncMock())
    member_id = uuid.uuid4()
    auth_id = str(uuid.uuid4())

    async def deliver(day, session_id=None, revision=1):
        sid = session_id or uuid.uuid4()
        await swim_rewards.reconcile_swim(
            db_session,
            swim_rewards.SwimVerification(
                member_id=member_id,
                member_auth_id=auth_id,
                session_id=sid,
                revision=revision,
                swim_date=date(2026, 11, day) if day else None,
            ),
        )
        return sid

    first = await deliver(22)
    await debit_wallet(
        db_session,
        member_auth_id=auth_id,
        amount=1,
        idempotency_key=f"spent-swim-{auth_id}",
        transaction_type=TransactionType.PURCHASE,
        description="Spend earned Bubble",
        service_source="sessions",
        reference_type="session_booking",
        reference_id=str(first),
    )
    await deliver(None, first, revision=2)
    wallet = (
        await db_session.scalars(select(Wallet).where(Wallet.member_auth_id == auth_id))
    ).one()
    assert wallet.balance == 0
    progress = await swim_rewards.member_progress(db_session, auth_id)
    assert (progress.bubbles_earned, progress.bubbles_credited) == (0, 1)

    await deliver(23)  # First subsequent earned Bubble pays down the offset
    assert wallet.balance == 0
    await deliver(24)  # Only the next qualifying day is newly credited
    await db_session.refresh(wallet)
    assert wallet.balance == 1
    progress = await swim_rewards.member_progress(db_session, auth_id)
    assert (progress.bubbles_earned, progress.bubbles_credited) == (2, 2)
