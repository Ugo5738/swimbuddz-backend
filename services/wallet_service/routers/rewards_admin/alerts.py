"""Reward alert CRUD + summary."""

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
