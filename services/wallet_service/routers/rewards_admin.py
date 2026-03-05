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

router = APIRouter(
    prefix="/admin/wallet/rewards",
    tags=["admin-rewards"],
    dependencies=[Depends(require_admin)],
)


# ---------------------------------------------------------------------------
# Reward Rules
# ---------------------------------------------------------------------------


@router.get("/rules", response_model=RewardRuleListResponse)
async def list_reward_rules(
    category: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """List all reward rules with optional filters."""
    query = select(RewardRule).order_by(
        RewardRule.priority.desc(), RewardRule.rule_name
    )
    count_query = select(func.count()).select_from(RewardRule)

    if category:
        query = query.where(RewardRule.category == category)
        count_query = count_query.where(RewardRule.category == category)
    if is_active is not None:
        query = query.where(RewardRule.is_active == is_active)
        count_query = count_query.where(RewardRule.is_active == is_active)

    total = (await db.execute(count_query)).scalar_one()
    result = await db.execute(query.offset(skip).limit(limit))
    rules = result.scalars().all()

    return RewardRuleListResponse(
        items=[RewardRuleResponse.model_validate(r) for r in rules],
        total=total,
    )


@router.post("/rules", response_model=RewardRuleResponse, status_code=201)
async def create_reward_rule(
    body: RewardRuleCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    admin: AuthUser = Depends(require_admin),
):
    """Create a new reward rule."""
    from services.wallet_service.models.enums import RewardCategory, RewardPeriod

    # Validate category
    try:
        category_enum = RewardCategory(body.category)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category '{body.category}'. Use: acquisition, retention, community, spending, academy.",
        )

    # Validate period if provided
    period_enum = None
    if body.period:
        try:
            period_enum = RewardPeriod(body.period)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid period '{body.period}'. Use: day, week, month, year.",
            )

    # Check for duplicate rule_name
    existing = await db.execute(
        select(RewardRule).where(RewardRule.rule_name == body.rule_name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"A rule with name '{body.rule_name}' already exists.",
        )

    rule = RewardRule(
        rule_name=body.rule_name,
        display_name=body.display_name,
        description=body.description,
        event_type=body.event_type,
        trigger_config=body.trigger_config,
        reward_bubbles=body.reward_bubbles,
        reward_description_template=body.reward_description_template,
        max_per_member_lifetime=body.max_per_member_lifetime,
        max_per_member_per_period=body.max_per_member_per_period,
        period=period_enum,
        category=category_enum,
        is_active=body.is_active,
        priority=body.priority,
        requires_admin_confirmation=body.requires_admin_confirmation,
        created_by=admin.user_id,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)

    logger.info("Admin %s created reward rule '%s'", admin.user_id, body.rule_name)
    return RewardRuleResponse.model_validate(rule)


@router.get("/rules/{rule_id}", response_model=RewardRuleDetailResponse)
async def get_reward_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Get a reward rule with usage stats."""
    result = await db.execute(select(RewardRule).where(RewardRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Reward rule not found")

    # Usage stats
    stats_result = await db.execute(
        select(
            func.count().label("total_grants"),
            func.coalesce(func.sum(MemberRewardHistory.bubbles_awarded), 0).label(
                "total_bubbles"
            ),
        )
        .select_from(MemberRewardHistory)
        .where(MemberRewardHistory.reward_rule_id == rule_id)
    )
    row = stats_result.one()

    resp = RewardRuleDetailResponse.model_validate(rule)
    resp.total_grants = row.total_grants
    resp.total_bubbles_distributed = row.total_bubbles
    return resp


@router.patch("/rules/{rule_id}", response_model=RewardRuleResponse)
async def update_reward_rule(
    rule_id: uuid.UUID,
    body: RewardRuleUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Update a reward rule (amount, caps, active status, etc.)."""
    result = await db.execute(select(RewardRule).where(RewardRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Reward rule not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(rule, field, value)

    await db.flush()
    await db.commit()
    await db.refresh(rule)

    return RewardRuleResponse.model_validate(rule)


# ---------------------------------------------------------------------------
# Reward Events
# ---------------------------------------------------------------------------


@router.get("/events", response_model=RewardEventListResponse)
async def list_reward_events(
    event_type: Optional[str] = Query(None),
    processed: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """List ingested events with optional filters."""
    query = select(WalletEvent).order_by(WalletEvent.created_at.desc())
    count_query = select(func.count()).select_from(WalletEvent)

    if event_type:
        query = query.where(WalletEvent.event_type == event_type)
        count_query = count_query.where(WalletEvent.event_type == event_type)
    if processed is not None:
        query = query.where(WalletEvent.processed == processed)
        count_query = count_query.where(WalletEvent.processed == processed)

    total = (await db.execute(count_query)).scalar_one()
    result = await db.execute(query.offset(skip).limit(limit))
    events = result.scalars().all()

    return RewardEventListResponse(
        items=[RewardEventListItem.model_validate(e) for e in events],
        total=total,
    )


@router.get("/events/failed", response_model=RewardEventListResponse)
async def list_failed_events(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """List events that failed processing."""
    query = (
        select(WalletEvent)
        .where(WalletEvent.processing_error.isnot(None))
        .order_by(WalletEvent.created_at.desc())
    )
    count_query = (
        select(func.count())
        .select_from(WalletEvent)
        .where(WalletEvent.processing_error.isnot(None))
    )

    total = (await db.execute(count_query)).scalar_one()
    result = await db.execute(query.offset(skip).limit(limit))
    events = result.scalars().all()

    return RewardEventListResponse(
        items=[RewardEventListItem.model_validate(e) for e in events],
        total=total,
    )


# ---------------------------------------------------------------------------
# Reward Stats
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Admin Event Submission
# ---------------------------------------------------------------------------


@router.post("/events/submit", response_model=EventIngestResponse)
async def admin_submit_event(
    body: AdminEventSubmitRequest,
    db: AsyncSession = Depends(get_async_db),
    admin: AuthUser = Depends(require_admin),
):
    """Admin submits a reward event on behalf of a member.

    Used for safety reports, content contributions, social shares,
    ride-share completions, and other ad-hoc reward triggers that
    don't have automated service hooks yet.

    Automatically sets admin_confirmed=True in event_data.
    """
    # Validate that at least one active rule matches this event_type
    result = await db.execute(
        select(func.count())
        .select_from(RewardRule)
        .where(
            RewardRule.event_type == body.event_type,
            RewardRule.is_active.is_(True),
        )
    )
    if result.scalar_one() == 0:
        raise HTTPException(
            status_code=400,
            detail=f"No active reward rules match event type '{body.event_type}'",
        )

    # Build event data with admin confirmation injected
    event_data = {**body.event_data, "admin_confirmed": True}
    if body.description:
        event_data["admin_notes"] = body.description
    event_data["submitted_by"] = admin.user_id

    now = datetime.now(timezone.utc)
    event_id = uuid.uuid4()
    idempotency_key = f"admin-event-{body.event_type}-{body.member_auth_id}-{event_id}"

    # Create the event record directly (we're in the same service)
    event = WalletEvent(
        event_id=event_id,
        event_type=body.event_type,
        member_auth_id=body.member_auth_id,
        service_source="admin",
        occurred_at=now,
        event_data=event_data,
        idempotency_key=idempotency_key,
    )
    db.add(event)
    await db.flush()

    # Process through the rewards engine
    try:
        grants = await process_event(event, db)
    except Exception:
        logger.exception(
            "Error processing admin-submitted event %s for %s",
            body.event_type,
            body.member_auth_id,
        )
        event.processing_error = "Unexpected error during processing"
        event.processed = True
        await db.flush()
        await db.commit()
        raise HTTPException(
            status_code=500,
            detail="Event accepted but reward processing failed.",
        )

    await db.commit()

    return EventIngestResponse(
        event_id=event.event_id,
        accepted=True,
        rewards_granted=len(grants),
        rewards=[
            RewardGrantItem(rule_name=g["rule_name"], bubbles=g["bubbles"])
            for g in grants
        ],
    )


# ---------------------------------------------------------------------------
# Anti-Abuse Alerts (Phase 3d)
# ---------------------------------------------------------------------------


@router.get("/alerts", response_model=RewardAlertListResponse)
async def list_alerts(
    status_filter: Optional[str] = Query(None, alias="status"),
    severity: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """List anti-abuse alerts with optional filters."""
    query = select(RewardAlert).order_by(RewardAlert.created_at.desc())
    count_query = select(func.count()).select_from(RewardAlert)

    if status_filter:
        query = query.where(RewardAlert.status == AlertStatus(status_filter))
        count_query = count_query.where(
            RewardAlert.status == AlertStatus(status_filter)
        )
    if severity:
        query = query.where(RewardAlert.severity == severity)
        count_query = count_query.where(RewardAlert.severity == severity)

    total = (await db.execute(count_query)).scalar_one()
    result = await db.execute(query.offset(skip).limit(limit))
    alerts = result.scalars().all()

    return RewardAlertListResponse(
        items=[RewardAlertResponse.model_validate(a) for a in alerts],
        total=total,
    )


@router.get("/alerts/summary", response_model=RewardAlertSummaryResponse)
async def get_alert_summary(
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Alert counts by status and severity."""
    # Counts by status
    status_result = await db.execute(
        select(
            RewardAlert.status,
            func.count().label("cnt"),
        ).group_by(RewardAlert.status)
    )
    status_counts = {row.status.value: row.cnt for row in status_result}

    # Counts by severity for open alerts
    severity_result = await db.execute(
        select(
            RewardAlert.status,
            RewardAlert.severity,
            func.count().label("cnt"),
        )
        .where(RewardAlert.status.in_([AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED]))
        .group_by(RewardAlert.status, RewardAlert.severity)
    )
    by_severity = [
        AlertSummaryItem(
            status=row.status.value,
            severity=row.severity.value,
            count=row.cnt,
        )
        for row in severity_result
    ]

    return RewardAlertSummaryResponse(
        total_open=status_counts.get("open", 0),
        total_acknowledged=status_counts.get("acknowledged", 0),
        total_resolved=status_counts.get("resolved", 0),
        total_dismissed=status_counts.get("dismissed", 0),
        by_severity=by_severity,
    )


@router.get("/alerts/{alert_id}", response_model=RewardAlertResponse)
async def get_alert(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Get a single alert by ID."""
    result = await db.execute(select(RewardAlert).where(RewardAlert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return RewardAlertResponse.model_validate(alert)


@router.patch("/alerts/{alert_id}", response_model=RewardAlertResponse)
async def update_alert(
    alert_id: uuid.UUID,
    body: RewardAlertUpdateRequest,
    db: AsyncSession = Depends(get_async_db),
    admin: AuthUser = Depends(require_admin),
):
    """Update alert status (acknowledge, resolve, dismiss)."""
    result = await db.execute(select(RewardAlert).where(RewardAlert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    try:
        new_status = AlertStatus(body.status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{body.status}'. Use: open, acknowledged, resolved, dismissed.",
        )

    alert.status = new_status
    if body.resolution_notes:
        alert.resolution_notes = body.resolution_notes
    if new_status in (AlertStatus.RESOLVED, AlertStatus.DISMISSED):
        alert.resolved_by = admin.user_id
        alert.resolved_at = datetime.now(timezone.utc)

    await db.flush()
    await db.commit()
    await db.refresh(alert)

    return RewardAlertResponse.model_validate(alert)


# ---------------------------------------------------------------------------
# Rewards Analytics (Phase 3d)
# ---------------------------------------------------------------------------


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
