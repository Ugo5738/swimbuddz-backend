"""Cap enforcement for the rewards engine.

Checks lifetime and periodic caps before granting a reward to a member.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models.enums import RewardPeriod
from services.wallet_service.models.rewards import MemberRewardHistory, RewardRule

logger = logging.getLogger(__name__)


def compute_period_key(period: RewardPeriod) -> str:
    """Compute the current period key for cap checking.

    Returns a string like "2026-02-27" (day), "2026-W09" (week),
    "2026-02" (month), or "2026" (year).
    """
    now = datetime.now(timezone.utc)
    if period == RewardPeriod.DAY:
        return now.strftime("%Y-%m-%d")
    if period == RewardPeriod.WEEK:
        return now.strftime("%G-W%V")
    if period == RewardPeriod.MONTH:
        return now.strftime("%Y-%m")
    if period == RewardPeriod.YEAR:
        return now.strftime("%Y")
    return now.strftime("%Y-%m")


async def check_lifetime_cap(
    db: AsyncSession, member_auth_id: str, rule: RewardRule
) -> bool:
    """Return True if the member is still under the lifetime cap for this rule.

    Returns True (eligible) if:
    - rule.max_per_member_lifetime is None (no cap), or
    - current grant count < cap
    """
    if rule.max_per_member_lifetime is None:
        return True

    result = await db.execute(
        select(func.count())
        .select_from(MemberRewardHistory)
        .where(
            MemberRewardHistory.member_auth_id == member_auth_id,
            MemberRewardHistory.reward_rule_id == rule.id,
        )
    )
    count = result.scalar_one()
    eligible = count < rule.max_per_member_lifetime
    if not eligible:
        logger.debug(
            "Lifetime cap reached for member=%s rule=%s (%d/%d)",
            member_auth_id,
            rule.rule_name,
            count,
            rule.max_per_member_lifetime,
        )
    return eligible


async def check_period_cap(
    db: AsyncSession, member_auth_id: str, rule: RewardRule
) -> bool:
    """Return True if the member is still under the period cap for this rule.

    Returns True (eligible) if:
    - rule.max_per_member_per_period is None (no periodic cap), or
    - rule.period is None (no period defined), or
    - current period grant count < cap
    """
    if rule.max_per_member_per_period is None or rule.period is None:
        return True

    period_key = compute_period_key(rule.period)

    result = await db.execute(
        select(func.count())
        .select_from(MemberRewardHistory)
        .where(
            MemberRewardHistory.member_auth_id == member_auth_id,
            MemberRewardHistory.reward_rule_id == rule.id,
            MemberRewardHistory.period_key == period_key,
        )
    )
    count = result.scalar_one()
    eligible = count < rule.max_per_member_per_period
    if not eligible:
        logger.debug(
            "Period cap reached for member=%s rule=%s period=%s (%d/%d)",
            member_auth_id,
            rule.rule_name,
            period_key,
            count,
            rule.max_per_member_per_period,
        )
    return eligible
