"""Settle swim credits under the same wallet row lock as other balance changes."""

from collections import Counter
from datetime import date, timedelta
import uuid
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from libs.common.config import get_settings
from libs.common.currency import NAIRA_PER_BUBBLE
from libs.common.datetime_utils import utc_now
from libs.common.ledger_client import post_journal_entry
from libs.common.logging import get_logger
from libs.common.service_client import internal_get
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from services.wallet_service.models import (
    GrantType,
    MemberRewardHistory,
    TransactionDirection,
    TransactionStatus,
    WalletTransaction,
    PromotionalBubbleGrant,
    TransactionType,
    Wallet,
    WalletLedgerPostFailure,
)
from services.wallet_service.models.swim import VerifiedSwim
from services.wallet_service.services.ledger_emit import build_wallet_post_kwargs
from services.wallet_service.services.wallet_ops import stage_credit
from services.wallet_service.services.hold_ops import active_held_amount

logger = get_logger(__name__)
RULE_ID = uuid.UUID("6a7d9ae3-1c10-48ce-8f7a-149782b5089c")
WEEKLY_LIMIT = 3
PROGRAMME_START_DATE = date(2026, 11, 22)
LAGOS = ZoneInfo("Africa/Lagos")


class SwimVerification(BaseModel):
    member_id: uuid.UUID
    member_auth_id: str = Field(min_length=1)
    session_id: uuid.UUID
    revision: int = Field(gt=0)
    swim_date: date | None
    session_title: str | None = None


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
    programme_start_date: date = PROGRAMME_START_DATE
    all_time_swims: int = 0
    all_time_active_weeks: int = 0
    all_time_best_streak: int = 0
    all_time_current_streak: int = 0
    all_time_available: bool = False


def _best_streak_from_weeks(week_mondays: list[date]) -> int:
    best = 0
    run = 0
    prev = None
    one_week = timedelta(weeks=1)
    for wk in sorted(week_mondays):
        if prev is not None and wk == prev + one_week:
            run += 1
        else:
            run = 1
        if run > best:
            best = run
        prev = wk
    return best


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
    best_streak = _best_streak_from_weeks(list(weeks.keys()))
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
        programme_start_date=PROGRAMME_START_DATE,
        all_time_swims=len(dates),
        all_time_active_weeks=len(weeks),
        all_time_best_streak=best_streak,
        all_time_current_streak=streak,
        all_time_available=False,
    )


async def member_progress(db, auth_id: str) -> SwimProgress:
    """Pure DB-only progress summary safe to run inside wallet row locks."""
    rows = (
        await db.execute(
            select(VerifiedSwim.swim_date, VerifiedSwim.issued)
            .join(Wallet, Wallet.id == VerifiedSwim.wallet_id)
            .where(Wallet.member_auth_id == auth_id)
        )
    ).all()
    return progress(rows, utc_now().astimezone(LAGOS).date())


async def fetch_all_time_milestones(auth_id: str) -> dict | None:
    """Best-effort fetch of all-time attended swim milestones from attendance-service."""
    try:
        response = await internal_get(
            service_url=get_settings().ATTENDANCE_SERVICE_URL,
            path=f"/internal/attendance/member-milestones/{auth_id}",
            calling_service="wallet",
            timeout=3.0,
        )
        if response.status_code == 200:
            return response.json()
    except Exception as exc:
        logger.warning(
            "Could not fetch all-time swim milestones for %s: %s", auth_id, exc
        )
    return None


async def member_progress_with_milestones(db, auth_id: str) -> SwimProgress:
    """Read-only member progress enriched with all-time attendance milestones."""
    stats = await member_progress(db, auth_id)
    milestones = await fetch_all_time_milestones(auth_id)
    if milestones and milestones.get("attendance_available"):
        return stats.model_copy(
            update={
                "all_time_swims": max(
                    int(milestones.get("total_attended_swims", 0)), stats.total_swims
                ),
                "all_time_active_weeks": max(
                    int(milestones.get("active_weeks", 0)), stats.active_weeks
                ),
                "all_time_best_streak": max(
                    int(milestones.get("best_streak_weeks", 0)),
                    stats.all_time_best_streak,
                ),
                "all_time_current_streak": max(
                    int(milestones.get("current_streak_weeks", 0)), stats.streak_weeks
                ),
                "all_time_available": True,
            }
        )
    return stats


def _format_swim_credit_description(
    swim_date: date | None, session_title: str | None
) -> str:
    title = (session_title or "").strip()
    if title and swim_date:
        return f"Swim Bubble · {title} ({swim_date})"
    if title:
        return f"Swim Bubble · {title}"
    if swim_date:
        return f"Swim Bubble · {swim_date}"
    return "Swim Bubble"


def _format_swim_reversal_description(
    prior_swim_date: date | None, session_title: str | None
) -> str:
    title = (session_title or "").strip()
    if title and prior_swim_date:
        return f"Swim Bubble reversed · {title} ({prior_swim_date})"
    if title:
        return f"Swim Bubble reversed · {title}"
    if prior_swim_date:
        return f"Swim Bubble reversed · {prior_swim_date}"
    return "Swim Bubble reversed · Attendance correction"


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
    prior_swim_date = fact.swim_date if fact is not None else None
    if fact is None:
        fact = VerifiedSwim(wallet_id=wallet.id, session_id=body.session_id, issued=0)
        db.add(fact)
    fact.revision = body.revision
    fact.swim_date = body.swim_date
    await db.flush()
    stats = await member_progress(db, body.member_auth_id)
    amount = stats.bubbles_earned - stats.bubbles_credited
    if amount < 0:
        # Revoke only unspent swim-reward grants, not purchased Bubbles or
        # unrelated promotional awards. Active checkout holds stay protected.
        outstanding = -amount
        grants = (
            await db.scalars(
                select(PromotionalBubbleGrant)
                .where(
                    PromotionalBubbleGrant.wallet_id == wallet.id,
                    PromotionalBubbleGrant.campaign_code == "verified_swim",
                    PromotionalBubbleGrant.bubbles_remaining > 0,
                    (
                        PromotionalBubbleGrant.expires_at.is_(None)
                        | (PromotionalBubbleGrant.expires_at > utc_now())
                    ),
                )
                .order_by(PromotionalBubbleGrant.created_at.desc())
            )
        ).all()
        available = max(wallet.balance - await active_held_amount(db, wallet.id), 0)
        reversal = min(
            outstanding, available, sum(grant.bubbles_remaining for grant in grants)
        )
        if reversal:
            remaining = reversal
            for grant in grants:
                take = min(grant.bubbles_remaining, remaining)
                if take:
                    grant.bubbles_remaining -= take
                    remaining -= take
                if not remaining:
                    break
            balance_before = wallet.balance
            wallet.balance -= reversal
            # Match the original source of the credit. The append-only wallet
            # transaction keeps the correction independently auditable.
            txn = WalletTransaction(
                wallet_id=wallet.id,
                idempotency_key=f"swim-reverse-{wallet.id}-{body.session_id}-{body.revision}",
                transaction_type=TransactionType.ADMIN_ADJUSTMENT,
                direction=TransactionDirection.DEBIT,
                amount=reversal,
                balance_before=balance_before,
                balance_after=wallet.balance,
                status=TransactionStatus.COMPLETED,
                description=_format_swim_reversal_description(
                    prior_swim_date, body.session_title
                ),
                service_source="attendance",
                initiated_by="attendance",
                reference_type="swim_reward_reversal",
                reference_id=str(body.session_id),
                txn_metadata={
                    "session_id": str(body.session_id),
                    "session_title": body.session_title,
                    "swim_date": str(prior_swim_date) if prior_swim_date else None,
                    "revision": body.revision,
                    "promo_bubbles": reversal,
                    "purchased_bubbles": 0,
                },
            )
            db.add(txn)
            # Previously issued credits can span sessions and weekly caps.
            # Track the reversal against issued amounts without deleting
            # historical grants or tampering with purchased balances.
            remaining = reversal
            issued_facts = (
                await db.scalars(
                    select(VerifiedSwim)
                    .where(
                        VerifiedSwim.wallet_id == wallet.id,
                        VerifiedSwim.issued > 0,
                    )
                    .order_by(VerifiedSwim.swim_date.desc().nulls_last())
                )
            ).all()
            for issued_fact in issued_facts:
                take = min(issued_fact.issued, remaining)
                issued_fact.issued -= take
                remaining -= take
                if not remaining:
                    break
            await db.flush()
            journal = build_wallet_post_kwargs(txn, body.member_auth_id)
            if journal is None:
                raise RuntimeError("Swim reversal must produce a ledger journal")
            delivery = WalletLedgerPostFailure(
                idempotency_key=f"wallet:swim-reversal:{txn.id}",
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
                logger.warning("Swim reversal journal %s queued: %s", txn.id, exc)
                delivery.last_error = str(exc)
            delivery.attempts += 1
            await db.commit()
            return
        # An already spent reward becomes a debt against future swim rewards.
        # Never debit purchased money or break current checkout holds.
        await db.commit()
        return
    if amount == 0:
        await db.commit()
        return

    session_title = (body.session_title or "").strip() or None
    grant = PromotionalBubbleGrant(
        id=uuid.uuid4(),
        wallet_id=wallet.id,
        member_auth_id=body.member_auth_id,
        grant_type=GrantType.LOYALTY_REWARD,
        bubbles_amount=amount,
        bubbles_remaining=amount,
        granted_by="attendance",
        reason=(
            f"{session_title} on {body.swim_date}"
            if session_title
            else f"Swim on {body.swim_date}"
        ),
        campaign_code="verified_swim",
    )
    db.add(grant)
    txn = stage_credit(
        db,
        wallet,
        amount=amount,
        transaction_type=TransactionType.REWARD,
        idempotency_key=f"swim-{wallet.id}-{body.session_id}-{body.revision}",
        description=_format_swim_credit_description(body.swim_date, session_title),
        service_source="attendance",
        initiated_by="attendance",
        reference_type="grant",
        reference_id=str(grant.id),
        metadata={
            "session_id": str(body.session_id),
            "session_title": session_title,
            "swim_date": str(body.swim_date),
        },
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
