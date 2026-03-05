"""Member-facing reward endpoints (history + rules)."""

from fastapi import APIRouter, Depends, Query
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models.rewards import MemberRewardHistory, RewardRule
from services.wallet_service.models.transaction import WalletTransaction
from services.wallet_service.schemas import RewardRuleListResponse, RewardRuleResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/wallet/rewards", tags=["rewards-member"])


# ---------------------------------------------------------------------------
# Reward History
# ---------------------------------------------------------------------------


@router.get("/history")
async def get_my_reward_history(
    limit: int = Query(20, ge=1, le=200),
    skip: int = Query(0, ge=0),
    offset: int | None = Query(None, ge=0),
    category: str | None = Query(None),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the current member's reward history."""
    effective_skip = offset if offset is not None else skip

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
    count_query = (
        select(func.count())
        .select_from(MemberRewardHistory)
        .outerjoin(RewardRule, RewardRule.id == MemberRewardHistory.reward_rule_id)
    )
    count_query = count_query.where(
        MemberRewardHistory.member_auth_id == current_user.user_id
    )

    if category:
        query = query.where(RewardRule.category == category)
        count_query = count_query.where(RewardRule.category == category)

    query = (
        query.order_by(MemberRewardHistory.created_at.desc())
        .offset(effective_skip)
        .limit(limit)
    )

    result = await db.execute(query)
    items = result.all()

    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    history = []
    for h, rule, transaction_description in items:
        category_value = None
        if rule and rule.category is not None:
            category_value = (
                rule.category.value
                if hasattr(rule.category, "value")
                else str(rule.category)
            )

        history.append(
            {
                "id": str(h.id),
                "rule_name": rule.rule_name if rule else "reward",
                "display_name": rule.display_name if rule else None,
                "category": category_value or "unknown",
                "bubbles_awarded": h.bubbles_awarded,
                "description": transaction_description
                or (rule.description if rule else None),
                "created_at": h.created_at.isoformat() if h.created_at else None,
            }
        )

    return {
        "history": history,
        "total": total,
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
