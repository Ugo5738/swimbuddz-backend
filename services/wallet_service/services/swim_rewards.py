"""Settle swim credits under the same wallet row lock as other balance changes."""

from collections import Counter
from datetime import date, timedelta
import uuid
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from libs.common.currency import NAIRA_PER_BUBBLE
from libs.common.datetime_utils import utc_now
from libs.common.ledger_client import post_journal_entry
from libs.common.logging import get_logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from services.wallet_service.models import (
    GrantType,
    MemberRewardHistory,
    PromotionalBubbleGrant,
    TransactionType,
    Wallet,
    WalletLedgerPostFailure,
)
from services.wallet_service.models.swim import VerifiedSwim
from services.wallet_service.services.ledger_emit import build_wallet_post_kwargs
from services.wallet_service.services.wallet_ops import stage_credit

logger = get_logger(__name__)
RULE_ID = uuid.UUID("6a7d9ae3-1c10-48ce-8f7a-149782b5089c")
WEEKLY_LIMIT = 3
LAGOS = ZoneInfo("Africa/Lagos")


class SwimVerification(BaseModel):
    member_id: uuid.UUID
    member_auth_id: str = Field(min_length=1)
    session_id: uuid.UUID
    revision: int = Field(gt=0)
    swim_date: date | None


class SwimProgress(BaseModel):
    total_swims: int
    active_weeks: int
    streak_weeks: int
    week_days: int
    weekly_limit: int
    today_counted: bool
    bubbles_earned: int
    bubbles_credited: int
    reward_value_naira: int
    next_week: date


def progress(rows, today: date) -> SwimProgress:
    dates = [row.swim_date for row in rows if row.swim_date is not None]
    days = set(dates)
    weeks = Counter(day - timedelta(days=day.weekday()) for day in days)
    monday = today - timedelta(days=today.weekday())
    week = timedelta(weeks=1)
    cursor = monday if monday in weeks else monday - week
    streak = 0
    while cursor in weeks:
        streak += 1
        cursor -= week
    earned = sum(min(count, WEEKLY_LIMIT) for count in weeks.values())
    return SwimProgress(
        total_swims=len(dates),
        active_weeks=len(weeks),
        streak_weeks=streak,
        week_days=min(WEEKLY_LIMIT, weeks[monday]),
        weekly_limit=WEEKLY_LIMIT,
        today_counted=today in days,
        bubbles_earned=earned,
        bubbles_credited=sum(row.issued for row in rows),
        reward_value_naira=earned * NAIRA_PER_BUBBLE,
        next_week=monday + week,
    )


async def member_progress(db, auth_id: str) -> SwimProgress:
    rows = (
        await db.execute(
            select(VerifiedSwim.swim_date, VerifiedSwim.issued)
            .join(Wallet, Wallet.id == VerifiedSwim.wallet_id)
            .where(Wallet.member_auth_id == auth_id)
        )
    ).all()
    return progress(rows, utc_now().astimezone(LAGOS).date())


async def reconcile_swim(db: AsyncSession, body: SwimVerification):
    today = utc_now().astimezone(LAGOS).date()
    if body.swim_date and body.swim_date > today:
        raise HTTPException(422, "A future swim cannot earn a Bubble.")
    await db.execute(
        insert(Wallet)
        .values(member_id=body.member_id, member_auth_id=body.member_auth_id)
        .on_conflict_do_nothing()
    )
    wallet = (
        await db.scalars(
            select(Wallet)
            .options(noload("*"))
            .where(Wallet.member_auth_id == body.member_auth_id)
            .with_for_update()
        )
    ).one_or_none()
    if wallet is None or wallet.member_id != body.member_id:
        raise HTTPException(409, "The swim member and wallet do not match.")
    fact = await db.get(VerifiedSwim, (wallet.id, body.session_id))
    if fact and fact.revision >= body.revision:
        return
    if fact is None:
        fact = VerifiedSwim(wallet_id=wallet.id, session_id=body.session_id, issued=0)
        db.add(fact)
    fact.revision = body.revision
    fact.swim_date = body.swim_date
    await db.flush()
    stats = await member_progress(db, body.member_auth_id)
    amount = stats.bubbles_earned - stats.bubbles_credited
    if amount <= 0:
        await db.commit()
        return

    grant = PromotionalBubbleGrant(
        id=uuid.uuid4(),
        wallet_id=wallet.id,
        member_auth_id=body.member_auth_id,
        grant_type=GrantType.LOYALTY_REWARD,
        bubbles_amount=amount,
        bubbles_remaining=amount,
        granted_by="attendance",
        reason=f"Confirmed swim on {body.swim_date}",
        campaign_code="verified_swim",
    )
    db.add(grant)
    txn = stage_credit(
        db,
        wallet,
        amount=amount,
        transaction_type=TransactionType.REWARD,
        idempotency_key=f"swim-{wallet.id}-{body.session_id}-{body.revision}",
        description=f"Verified swim · {body.swim_date}",
        service_source="attendance",
        initiated_by="attendance",
        reference_type="grant",
        reference_id=str(grant.id),
        metadata={"session_id": str(body.session_id), "swim_date": str(body.swim_date)},
    )
    await db.flush()
    grant.transaction_id = txn.id
    fact.issued += amount
    db.add(
        MemberRewardHistory(
            member_auth_id=body.member_auth_id,
            reward_rule_id=RULE_ID,
            transaction_id=txn.id,
            bubbles_awarded=amount,
            period_key=str(body.swim_date - timedelta(days=body.swim_date.weekday())),
        )
    )
    # Persist the journal instruction with the wallet credit, including across process restarts.
    journal = build_wallet_post_kwargs(txn, body.member_auth_id)
    delivery = WalletLedgerPostFailure(
        idempotency_key=f"wallet:reward:{txn.id}",
        source_reference=str(txn.id),
        payload=jsonable_encoder(journal),
        attempts=0,
        status="pending",
    )
    db.add(delivery)
    await db.commit()
    try:
        await post_journal_entry(calling_service="wallet", **journal)
        delivery.status = "replayed"
    except Exception as exc:
        logger.warning("Swim credit %s queued for ledger replay: %s", txn.id, exc)
        delivery.last_error = str(exc)
    delivery.attempts += 1
    await db.commit()
