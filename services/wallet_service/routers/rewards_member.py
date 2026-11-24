"""Member-facing reward endpoints (history + rules)."""

import re

from fastapi import APIRouter, Depends, Query
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models.enums import TransactionStatus
from services.wallet_service.models.rewards import MemberRewardHistory, RewardRule
from services.wallet_service.models.transaction import WalletTransaction
from services.wallet_service.models.wallet import Wallet
from services.wallet_service.schemas import RewardRuleListResponse, RewardRuleResponse
from services.wallet_service.services.swim_rewards import (
    SwimProgress,
    member_progress_with_milestones,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/wallet/rewards", tags=["rewards-member"])

_UUID_SUFFIX_RE = re.compile(
    r"^Swim reward reversal\s*·\s*[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _clean_reversal_description(txn: WalletTransaction) -> str:
    """Ensure member-facing reversal copy never shows raw internal session UUIDs."""
    meta = txn.txn_metadata or {}
    raw = (txn.description or "").strip()
    if raw and not _UUID_SUFFIX_RE.match(raw):
        return raw
    title = (meta.get("session_title") or "").strip()
    swim_date = meta.get("swim_date")
    if title and swim_date:
        return f"Swim Bubble reversed · {title} ({swim_date})"
    if title:
        return f"Swim Bubble reversed · {title}"
    if swim_date:
        return f"Swim Bubble reversed · {swim_date}"
    return "Swim Bubble reversed · Attendance correction"


# ---------------------------------------------------------------------------
# Reward History
# ---------------------------------------------------------------------------


@router.get("/swims", response_model=SwimProgress)
async def get_swim_progress(
    user: AuthUser = Depends(get_current_user), db: AsyncSession = Depends(get_async_db)
):
    return await member_progress_with_milestones(db, user.user_id)


@router.get("/history")
async def get_my_reward_history(
    limit: int = Query(20, ge=1, le=200),
    skip: int = Query(0, ge=0),
    offset: int | None = Query(None, ge=0),
    category: str | None = Query(None),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the current member's reward history, including swim reward reversals
    and true server-computed net lifetime totals."""
    effective_skip = offset if offset is not None else skip
    fetch_window = effective_skip + limit

    query = (
        select(
            MemberRewardHistory,
            RewardRule,
            WalletTransaction.description.label("transaction_description"),
        )
        .outerjoin(RewardRule, RewardRule.id == MemberRewardHistory.reward_rule_id)
        .outerjoin(
            WalletTransaction,
            WalletTransaction.id == MemberRewardHistory.transaction_id,
        )
        .where(MemberRewardHistory.member_auth_id == current_user.user_id)
    )
    totals_query = (
        select(
            func.count().label("row_count"),
            func.coalesce(func.sum(MemberRewardHistory.bubbles_awarded), 0).label(
                "bubbles_sum"
            ),
        )
        .select_from(MemberRewardHistory)
        .outerjoin(RewardRule, RewardRule.id == MemberRewardHistory.reward_rule_id)
        .where(MemberRewardHistory.member_auth_id == current_user.user_id)
    )

    if category:
        query = query.where(RewardRule.category == category)
        totals_query = totals_query.where(RewardRule.category == category)

    query = query.order_by(MemberRewardHistory.created_at.desc()).limit(fetch_window)

    result = await db.execute(query)
    items = result.all()

    totals_row = (await db.execute(totals_query)).one()
    total = int(totals_row.row_count or 0)
    total_bubbles_awarded = int(totals_row.bubbles_sum or 0)

    merged_entries: list[tuple] = []
    for h, rule, transaction_description in items:
        category_value = None
        if rule and rule.category is not None:
            category_value = (
                rule.category.value
                if hasattr(rule.category, "value")
                else str(rule.category)
            )

        merged_entries.append(
            (
                h.created_at,
                {
                    "id": str(h.id),
                    "rule_name": rule.rule_name if rule else "reward",
                    "display_name": rule.display_name if rule else None,
                    "category": category_value or "unknown",
                    "bubbles_awarded": h.bubbles_awarded,
                    "description": transaction_description
                    or (rule.description if rule else None),
                    "created_at": h.created_at.isoformat() if h.created_at else None,
                    "is_reversal": False,
                },
            )
        )

    include_reversals = not category or category == "retention"
    if include_reversals:
        reversal_filter = (
            Wallet.member_auth_id == current_user.user_id,
            WalletTransaction.reference_type == "swim_reward_reversal",
            WalletTransaction.status == TransactionStatus.COMPLETED,
        )
        reversal_totals_row = (
            await db.execute(
                select(
                    func.count().label("row_count"),
                    func.coalesce(func.sum(WalletTransaction.amount), 0).label(
                        "reversed_sum"
                    ),
                )
                .select_from(WalletTransaction)
                .join(Wallet, Wallet.id == WalletTransaction.wallet_id)
                .where(*reversal_filter)
            )
        ).one()
        reversal_count = int(reversal_totals_row.row_count or 0)
        reversal_sum = int(reversal_totals_row.reversed_sum or 0)

        if reversal_count > 0:
            total += reversal_count
            total_bubbles_awarded -= reversal_sum
            reversal_txns = (
                await db.scalars(
                    select(WalletTransaction)
                    .join(Wallet, Wallet.id == WalletTransaction.wallet_id)
                    .where(*reversal_filter)
                    .order_by(WalletTransaction.created_at.desc())
                    .limit(fetch_window)
                )
            ).all()
            for txn in reversal_txns:
                merged_entries.append(
                    (
                        txn.created_at,
                        {
                            "id": str(txn.id),
                            "rule_name": "verified_swim_day_reversal",
                            "display_name": "Swim Bubble reversed",
                            "category": "retention",
                            "bubbles_awarded": -int(txn.amount),
                            "description": _clean_reversal_description(txn),
                            "created_at": txn.created_at.isoformat()
                            if txn.created_at
                            else None,
                            "is_reversal": True,
                        },
                    )
                )

    merged_entries.sort(
        key=lambda item: item[0].timestamp() if item[0] is not None else 0.0,
        reverse=True,
    )
    history = [entry for _, entry in merged_entries[effective_skip:fetch_window]]

    return {
        "history": history,
        "total": total,
        "total_bubbles_awarded": total_bubbles_awarded,
    }


# ---------------------------------------------------------------------------
# Reward Rules (public list of active rules)
# ---------------------------------------------------------------------------


@router.get("/rules", response_model=RewardRuleListResponse)
async def list_active_reward_rules(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List active reward rules visible to members."""
    query = (
        select(RewardRule)
        .where(RewardRule.is_active.is_(True))
        .order_by(RewardRule.priority.desc(), RewardRule.display_name)
        .offset(skip)
        .limit(limit)
    )
    count_query = (
        select(func.count())
        .select_from(RewardRule)
        .where(RewardRule.is_active.is_(True))
    )

    total = (await db.execute(count_query)).scalar_one()
    result = await db.execute(query)
    rules = result.scalars().all()

    return RewardRuleListResponse(
        items=[RewardRuleResponse.model_validate(r) for r in rules],
        total=total,
    )
