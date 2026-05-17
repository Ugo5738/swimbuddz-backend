"""Reward rule CRUD (list, create, get, update)."""

"""Admin rewards management endpoints — rules, events, and stats."""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models.rewards import (
    MemberRewardHistory,
    RewardRule,
)
from services.wallet_service.schemas.rewards import (
    RewardRuleCreateRequest,
    RewardRuleDetailResponse,
    RewardRuleListResponse,
    RewardRuleResponse,
    RewardRuleUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


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
