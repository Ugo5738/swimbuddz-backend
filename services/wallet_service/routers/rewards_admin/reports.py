"""Reward stats + analytics (read-only reports)."""

"""Admin rewards management endpoints — rules, events, and stats."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models.enums import AlertStatus
from services.wallet_service.models.rewards import (
    MemberRewardHistory,
    RewardAlert,
    RewardRule,
    WalletEvent,
)
from services.wallet_service.schemas.rewards import (
    AdminEventSubmitRequest,
    AlertSummaryItem,
    EventIngestResponse,
    EventTypeCount,
    RewardAlertListResponse,
    RewardAlertResponse,
    RewardAlertSummaryResponse,
    RewardAlertUpdateRequest,
    RewardAnalyticsResponse,
    RewardCategoryStats,
    RewardEventListItem,
    RewardEventListResponse,
    RewardGrantItem,
    RewardRuleCreateRequest,
    RewardRuleDetailResponse,
    RewardRuleListResponse,
    RewardRuleResponse,
    RewardRuleUpdateRequest,
    RewardStatsResponse,
    TopRuleUsage,
)
from services.wallet_service.services.rewards_engine import process_event

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/stats", response_model=RewardStatsResponse)
async def get_reward_stats(
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Dashboard stats for the rewards engine."""
    # Active rules count
    active_rules = (
        await db.execute(
            select(func.count())
            .select_from(RewardRule)
            .where(RewardRule.is_active.is_(True))
        )
    ).scalar_one()

    # Event counts
    processed_count = (
        await db.execute(
            select(func.count())
            .select_from(WalletEvent)
            .where(WalletEvent.processed.is_(True))
        )
    ).scalar_one()

    pending_count = (
        await db.execute(
            select(func.count())
            .select_from(WalletEvent)
            .where(WalletEvent.processed.is_(False))
        )
    ).scalar_one()

    # Total bubbles distributed via rewards
    total_bubbles = (
        await db.execute(
            select(func.coalesce(func.sum(MemberRewardHistory.bubbles_awarded), 0))
        )
    ).scalar_one()

    # Events by type (top 10)
    events_by_type_result = await db.execute(
        select(
            WalletEvent.event_type,
            func.count().label("cnt"),
        )
        .group_by(WalletEvent.event_type)
        .order_by(func.count().desc())
        .limit(10)
    )
    events_by_type = [
        EventTypeCount(event_type=row.event_type, count=row.cnt)
        for row in events_by_type_result
    ]

    # Top rules by usage (top 10)
    top_rules_result = await db.execute(
        select(
            RewardRule.rule_name,
            RewardRule.display_name,
            func.count().label("total_grants"),
            func.coalesce(func.sum(MemberRewardHistory.bubbles_awarded), 0).label(
                "total_bubbles"
            ),
        )
        .join(MemberRewardHistory, MemberRewardHistory.reward_rule_id == RewardRule.id)
        .group_by(RewardRule.id, RewardRule.rule_name, RewardRule.display_name)
        .order_by(func.count().desc())
        .limit(10)
    )
    top_rules = [
        TopRuleUsage(
            rule_name=row.rule_name,
            display_name=row.display_name,
            total_grants=row.total_grants,
            total_bubbles=row.total_bubbles,
        )
        for row in top_rules_result
    ]

    return RewardStatsResponse(
        total_rules_active=active_rules,
        total_events_processed=processed_count,
        total_events_pending=pending_count,
        total_bubbles_distributed=total_bubbles,
        events_by_type=events_by_type,
        top_rules_by_usage=top_rules,
    )

@router.get("/analytics", response_model=RewardAnalyticsResponse)
async def get_reward_analytics(
    period_start: Optional[datetime] = Query(None),
    period_end: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Detailed rewards analytics with category breakdown."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    if not period_end:
        period_end = now
    if not period_start:
        period_start = now - timedelta(days=30)

    # Total events in period
    total_events = (
        await db.execute(
            select(func.count())
            .select_from(WalletEvent)
            .where(
                WalletEvent.created_at >= period_start,
                WalletEvent.created_at <= period_end,
            )
        )
    ).scalar_one()

    # Total rewards + bubbles in period
    rewards_result = await db.execute(
        select(
            func.count().label("total_grants"),
            func.coalesce(func.sum(MemberRewardHistory.bubbles_awarded), 0).label(
                "total_bubbles"
            ),
            func.count(func.distinct(MemberRewardHistory.member_auth_id)).label(
                "unique_members"
            ),
        )
        .select_from(MemberRewardHistory)
        .where(
            MemberRewardHistory.created_at >= period_start,
            MemberRewardHistory.created_at <= period_end,
        )
    )
    rrow = rewards_result.one()
    total_grants = rrow.total_grants
    total_bubbles = rrow.total_bubbles
    unique_members = rrow.unique_members

    avg_per_member = total_bubbles / unique_members if unique_members > 0 else 0.0

    # By category
    cat_result = await db.execute(
        select(
            RewardRule.category,
            func.count().label("total_grants"),
            func.coalesce(func.sum(MemberRewardHistory.bubbles_awarded), 0).label(
                "total_bubbles"
            ),
            func.count(func.distinct(MemberRewardHistory.member_auth_id)).label(
                "unique_members"
            ),
        )
        .join(MemberRewardHistory, MemberRewardHistory.reward_rule_id == RewardRule.id)
        .where(
            MemberRewardHistory.created_at >= period_start,
            MemberRewardHistory.created_at <= period_end,
        )
        .group_by(RewardRule.category)
        .order_by(func.sum(MemberRewardHistory.bubbles_awarded).desc())
    )
    by_category = [
        RewardCategoryStats(
            category=row.category.value,
            total_grants=row.total_grants,
            total_bubbles=row.total_bubbles,
            unique_members=row.unique_members,
        )
        for row in cat_result
    ]

    # Top event types in period
    event_type_result = await db.execute(
        select(
            WalletEvent.event_type,
            func.count().label("cnt"),
        )
        .where(
            WalletEvent.created_at >= period_start,
            WalletEvent.created_at <= period_end,
        )
        .group_by(WalletEvent.event_type)
        .order_by(func.count().desc())
        .limit(10)
    )
    top_event_types = [
        EventTypeCount(event_type=row.event_type, count=row.cnt)
        for row in event_type_result
    ]

    return RewardAnalyticsResponse(
        period_start=period_start,
        period_end=period_end,
        total_events=total_events,
        total_rewards_granted=total_grants,
        total_bubbles_distributed=total_bubbles,
        unique_members_rewarded=unique_members,
        by_category=by_category,
        avg_bubbles_per_member=round(avg_per_member, 1),
        top_event_types=top_event_types,
    )
