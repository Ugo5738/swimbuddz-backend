"""Anti-abuse detection for the rewards engine.

Called after each successful reward grant to check for abuse signals
defined in REWARDS_ENGINE_DESIGN.md Section 13. Creates RewardAlert
records for admin review.

All checks are best-effort — exceptions are logged but never block
the reward grant that already happened.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models.enums import AlertSeverity, AlertStatus
from services.wallet_service.models.rewards import (
    MemberRewardHistory,
    RewardAlert,
    RewardRule,
    WalletEvent,
)

logger = logging.getLogger(__name__)

# Thresholds from design doc Section 13
RAPID_SAME_REWARD_THRESHOLD = 3  # >3 grants of same rule in 1 hour
DAILY_BUBBLES_THRESHOLD = 100  # >100 Bubbles in 1 day
FAILURE_RATE_THRESHOLD = 0.05  # >5% failure rate in 1 hour
FAILURE_RATE_MIN_EVENTS = 20  # Minimum events to evaluate failure rate


async def check_for_abuse(
    member_auth_id: str,
    rule: RewardRule,
    event: WalletEvent,
    db: AsyncSession,
) -> None:
    """Run all abuse detection checks after a reward grant.

    Best-effort: catches all exceptions and logs warnings.
    """
    try:
        await _check_rapid_same_reward(member_auth_id, rule, db)
        await _check_daily_bubbles_limit(member_auth_id, db)
        await _check_failure_rate(db)
    except Exception:
        logger.exception(
            "Abuse detection error for member=%s rule=%s",
            member_auth_id,
            rule.rule_name,
        )


async def _check_rapid_same_reward(
    member_auth_id: str,
    rule: RewardRule,
    db: AsyncSession,
) -> None:
    """Flag if a member earned the same reward >3 times in 1 hour."""
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

    count = (
        await db.execute(
            select(func.count())
            .select_from(MemberRewardHistory)
            .where(
                MemberRewardHistory.member_auth_id == member_auth_id,
                MemberRewardHistory.reward_rule_id == rule.id,
                MemberRewardHistory.created_at >= one_hour_ago,
            )
        )
    ).scalar_one()

    if count <= RAPID_SAME_REWARD_THRESHOLD:
        return

    # Check for existing open alert of same type for this member+rule
    existing = (
        await db.execute(
            select(func.count())
            .select_from(RewardAlert)
            .where(
                RewardAlert.alert_type == "rapid_same_reward",
                RewardAlert.member_auth_id == member_auth_id,
                RewardAlert.status == AlertStatus.OPEN,
                RewardAlert.created_at >= one_hour_ago,
            )
        )
    ).scalar_one()
    if existing > 0:
        return

    alert = RewardAlert(
        alert_type="rapid_same_reward",
        severity=AlertSeverity.MEDIUM,
        member_auth_id=member_auth_id,
        title=f"Rapid reward: {rule.display_name}",
        description=(
            f"Member earned '{rule.display_name}' {count} times in the last hour "
            f"(threshold: {RAPID_SAME_REWARD_THRESHOLD})."
        ),
        alert_data={
            "rule_id": str(rule.id),
            "rule_name": rule.rule_name,
            "count_in_hour": count,
            "threshold": RAPID_SAME_REWARD_THRESHOLD,
        },
    )
    db.add(alert)
    await db.flush()
    logger.warning(
        "Abuse alert: rapid_same_reward for member=%s rule=%s count=%d",
        member_auth_id,
        rule.rule_name,
        count,
    )


async def _check_daily_bubbles_limit(
    member_auth_id: str,
    db: AsyncSession,
) -> None:
    """Flag if a member earned >100 Bubbles in rewards today."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    total_today = (
        await db.execute(
            select(
                func.coalesce(func.sum(MemberRewardHistory.bubbles_awarded), 0)
            ).where(
                MemberRewardHistory.member_auth_id == member_auth_id,
                MemberRewardHistory.created_at >= today_start,
            )
        )
    ).scalar_one()

    if total_today <= DAILY_BUBBLES_THRESHOLD:
        return

    # Check for existing open alert today
    existing = (
        await db.execute(
            select(func.count())
            .select_from(RewardAlert)
            .where(
                RewardAlert.alert_type == "daily_limit_exceeded",
                RewardAlert.member_auth_id == member_auth_id,
                RewardAlert.status == AlertStatus.OPEN,
                RewardAlert.created_at >= today_start,
            )
        )
    ).scalar_one()
    if existing > 0:
        return

    alert = RewardAlert(
        alert_type="daily_limit_exceeded",
        severity=AlertSeverity.HIGH,
        member_auth_id=member_auth_id,
        title=f"High daily rewards: {total_today} Bubbles",
        description=(
            f"Member earned {total_today} Bubbles in rewards today "
            f"(threshold: {DAILY_BUBBLES_THRESHOLD})."
        ),
        alert_data={
            "total_bubbles_today": total_today,
            "threshold": DAILY_BUBBLES_THRESHOLD,
        },
    )
    db.add(alert)
    await db.flush()
    logger.warning(
        "Abuse alert: daily_limit_exceeded for member=%s total=%d",
        member_auth_id,
        total_today,
    )


async def _check_failure_rate(db: AsyncSession) -> None:
    """Flag if event processing failure rate exceeds 5% in the last hour.

    System-wide check (no member_auth_id).
    """
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

    result = await db.execute(
        select(
            func.count().label("total"),
            func.count()
            .filter(WalletEvent.processing_error.isnot(None))
            .label("failed"),
        )
        .select_from(WalletEvent)
        .where(WalletEvent.created_at >= one_hour_ago)
    )
    row = result.one()
    total = row.total
    failed = row.failed

    if total < FAILURE_RATE_MIN_EVENTS:
        return

    rate = failed / total
    if rate <= FAILURE_RATE_THRESHOLD:
        return

    # Check for existing open alert in last hour
    existing = (
        await db.execute(
            select(func.count())
            .select_from(RewardAlert)
            .where(
                RewardAlert.alert_type == "high_failure_rate",
                RewardAlert.member_auth_id.is_(None),
                RewardAlert.status == AlertStatus.OPEN,
                RewardAlert.created_at >= one_hour_ago,
            )
        )
    ).scalar_one()
    if existing > 0:
        return

    alert = RewardAlert(
        alert_type="high_failure_rate",
        severity=AlertSeverity.MEDIUM,
        title=f"High event failure rate: {rate:.1%}",
        description=(
            f"Event processing failure rate is {rate:.1%} "
            f"({failed}/{total} events in the last hour). "
            f"Threshold: {FAILURE_RATE_THRESHOLD:.0%}."
        ),
        alert_data={
            "total_events": total,
            "failed_events": failed,
            "failure_rate": round(rate, 4),
            "threshold": FAILURE_RATE_THRESHOLD,
        },
    )
    db.add(alert)
    await db.flush()
    logger.warning(
        "Abuse alert: high_failure_rate rate=%.1f%% (%d/%d)",
        rate * 100,
        failed,
        total,
    )
