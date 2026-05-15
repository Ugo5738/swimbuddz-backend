"""Reward event lookups + admin event submission."""

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
