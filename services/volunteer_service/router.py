"""Member-facing volunteer endpoints."""

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.volunteer_service.models import (
    OpportunityStatus,
    SlotStatus,
    VolunteerHoursLog,
    VolunteerOpportunity,
    VolunteerProfile,
    VolunteerReward,
    VolunteerRole,
    VolunteerSlot,
    VolunteerTier,
)
from services.volunteer_service.schemas import (
    HoursSummaryResponse,
    LeaderboardEntry,
    VolunteerHoursLogResponse,
    VolunteerOpportunityResponse,
    VolunteerProfileCreate,
    VolunteerProfileResponse,
    VolunteerProfileUpdate,
    VolunteerRewardResponse,
    VolunteerRoleResponse,
    VolunteerSlotResponse,
)
from services.volunteer_service.services import (
    is_late_cancellation,
    next_recognition_hours_needed,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/volunteers", tags=["volunteers"])


# ── Helpers ─────────────────────────────────────────────────────────


async def _get_member_id(user: AuthUser, db: AsyncSession) -> uuid.UUID:
    """Resolve auth_id → member UUID."""
    row = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": user.user_id},
    )
    member = row.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    return member


async def _enrich_opportunity(opp: VolunteerOpportunity) -> dict:
    """Add role_title and role_category enrichment."""
    data = {c.key: getattr(opp, c.key) for c in opp.__table__.columns}
    data["role_title"] = opp.role.title if opp.role else None
    data["role_category"] = opp.role.category.value if opp.role else None
    return data


# ── Roles ───────────────────────────────────────────────────────────


@router.get("/roles", response_model=list[VolunteerRoleResponse])
async def list_roles(
    active_only: bool = True,
    db: AsyncSession = Depends(get_async_db),
):
    """List volunteer roles (public)."""
    q = select(VolunteerRole).order_by(VolunteerRole.sort_order)
    if active_only:
        q = q.where(VolunteerRole.is_active.is_(True))
    rows = (await db.execute(q)).scalars().all()

    results = []
    for role in rows:
        count = (
            await db.execute(
                select(func.count(VolunteerProfile.id)).where(
                    VolunteerProfile.is_active.is_(True),
                    VolunteerProfile.preferred_roles.any(str(role.id)),
                )
            )
        ).scalar() or 0
        data = {c.key: getattr(role, c.key) for c in role.__table__.columns}
        data["active_volunteers_count"] = count
        results.append(data)
    return results


@router.get("/roles/{role_id}", response_model=VolunteerRoleResponse)
async def get_role(role_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)):
    """Get a single role."""
    role = (
        await db.execute(select(VolunteerRole).where(VolunteerRole.id == role_id))
    ).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    data = {c.key: getattr(role, c.key) for c in role.__table__.columns}
    data["active_volunteers_count"] = 0
    return data


# ── Profile ─────────────────────────────────────────────────────────


@router.get("/profile/me", response_model=VolunteerProfileResponse)
async def get_my_profile(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Get my volunteer profile."""
    member_id = await _get_member_id(user, db)
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=404, detail="Volunteer profile not found. Register first."
        )
    return profile


@router.post("/profile/me", response_model=VolunteerProfileResponse, status_code=201)
async def register_as_volunteer(
    data: VolunteerProfileCreate,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Register as a volunteer."""
    member_id = await _get_member_id(user, db)

    existing = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Already registered as a volunteer")

    profile = VolunteerProfile(
        member_id=member_id,
        preferred_roles=data.preferred_roles,
        available_days=data.available_days,
        notes=data.notes,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.patch("/profile/me", response_model=VolunteerProfileResponse)
async def update_my_profile(
    data: VolunteerProfileUpdate,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Update my volunteer preferences."""
    member_id = await _get_member_id(user, db)
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Volunteer profile not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile


# ── Opportunities ───────────────────────────────────────────────────


@router.get("/opportunities", response_model=list[VolunteerOpportunityResponse])
async def list_opportunities(
    status_filter: Optional[OpportunityStatus] = Query(None, alias="status"),
    role_id: Optional[uuid.UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
):
    """List volunteer opportunities (open ones visible to all authenticated members)."""
    q = (
        select(VolunteerOpportunity)
        .options(selectinload(VolunteerOpportunity.role))
        .order_by(VolunteerOpportunity.date.asc())
        .offset(skip)
        .limit(limit)
    )

    if status_filter:
        q = q.where(VolunteerOpportunity.status == status_filter)
    else:
        # Default: show open and in_progress
        q = q.where(
            VolunteerOpportunity.status.in_(
                [
                    OpportunityStatus.OPEN,
                    OpportunityStatus.IN_PROGRESS,
                ]
            )
        )

    if role_id:
        q = q.where(VolunteerOpportunity.role_id == role_id)
    if from_date:
        q = q.where(VolunteerOpportunity.date >= from_date)
    if to_date:
        q = q.where(VolunteerOpportunity.date <= to_date)

    rows = (await db.execute(q)).scalars().all()
    return [await _enrich_opportunity(opp) for opp in rows]


@router.get(
    "/opportunities/upcoming", response_model=list[VolunteerOpportunityResponse]
)
async def list_upcoming_opportunities(
    db: AsyncSession = Depends(get_async_db),
):
    """List opportunities in the next 14 days."""
    today = date.today()
    end = today + timedelta(days=14)
    q = (
        select(VolunteerOpportunity)
        .options(selectinload(VolunteerOpportunity.role))
        .where(
            VolunteerOpportunity.date >= today,
            VolunteerOpportunity.date <= end,
            VolunteerOpportunity.status.in_(
                [
                    OpportunityStatus.OPEN,
                    OpportunityStatus.IN_PROGRESS,
                ]
            ),
        )
        .order_by(VolunteerOpportunity.date.asc())
    )
    rows = (await db.execute(q)).scalars().all()
    return [await _enrich_opportunity(opp) for opp in rows]


@router.get("/opportunities/{opp_id}", response_model=VolunteerOpportunityResponse)
async def get_opportunity(
    opp_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get opportunity detail."""
    opp = (
        await db.execute(
            select(VolunteerOpportunity)
            .options(selectinload(VolunteerOpportunity.role))
            .where(VolunteerOpportunity.id == opp_id)
        )
    ).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return await _enrich_opportunity(opp)


# ── Slot Claiming ───────────────────────────────────────────────────


@router.post(
    "/opportunities/{opp_id}/claim",
    response_model=VolunteerSlotResponse,
    status_code=201,
)
async def claim_slot(
    opp_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Claim a volunteer slot on an opportunity."""
    member_id = await _get_member_id(user, db)

    opp = (
        await db.execute(
            select(VolunteerOpportunity).where(VolunteerOpportunity.id == opp_id)
        )
    ).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if opp.status not in (OpportunityStatus.OPEN, OpportunityStatus.IN_PROGRESS):
        raise HTTPException(
            status_code=400, detail="Opportunity is not accepting claims"
        )
    if opp.slots_filled >= opp.slots_needed:
        raise HTTPException(status_code=400, detail="All slots are filled")

    # Check if already claimed
    existing = (
        await db.execute(
            select(VolunteerSlot).where(
                VolunteerSlot.opportunity_id == opp_id,
                VolunteerSlot.member_id == member_id,
                VolunteerSlot.status.in_(
                    [
                        SlotStatus.CLAIMED,
                        SlotStatus.APPROVED,
                    ]
                ),
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="You already have an active slot for this opportunity",
        )

    # Check volunteer profile + tier
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=400, detail="Register as a volunteer first")

    tier_order = {
        VolunteerTier.TIER_1: 1,
        VolunteerTier.TIER_2: 2,
        VolunteerTier.TIER_3: 3,
    }
    if tier_order.get(profile.tier, 1) < tier_order.get(opp.min_tier, 1):
        raise HTTPException(
            status_code=403,
            detail=f"This opportunity requires {opp.min_tier.value} or higher",
        )

    # Create slot
    initial_status = (
        SlotStatus.APPROVED
        if opp.opportunity_type.value == "open_claim"
        else SlotStatus.CLAIMED
    )
    slot = VolunteerSlot(
        opportunity_id=opp_id,
        member_id=member_id,
        status=initial_status,
        approved_at=(
            datetime.now(timezone.utc)
            if initial_status == SlotStatus.APPROVED
            else None
        ),
    )
    db.add(slot)

    # Update slots_filled
    opp.slots_filled += 1
    if opp.slots_filled >= opp.slots_needed:
        opp.status = OpportunityStatus.FILLED

    await db.commit()
    await db.refresh(slot)
    return slot


@router.delete("/opportunities/{opp_id}/claim", status_code=204)
async def cancel_my_claim(
    opp_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Cancel my claim on an opportunity."""
    member_id = await _get_member_id(user, db)

    slot = (
        await db.execute(
            select(VolunteerSlot).where(
                VolunteerSlot.opportunity_id == opp_id,
                VolunteerSlot.member_id == member_id,
                VolunteerSlot.status.in_([SlotStatus.CLAIMED, SlotStatus.APPROVED]),
            )
        )
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="No active claim found")

    opp = (
        await db.execute(
            select(VolunteerOpportunity).where(VolunteerOpportunity.id == opp_id)
        )
    ).scalar_one_or_none()

    # Track late cancellation
    if opp and is_late_cancellation(
        opp.date, opp.start_time, opp.cancellation_deadline_hours
    ):
        profile = (
            await db.execute(
                select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
            )
        ).scalar_one_or_none()
        if profile:
            profile.total_late_cancellations += 1

    slot.status = SlotStatus.CANCELLED
    slot.cancelled_at = datetime.now(timezone.utc)

    # Decrement slots_filled
    if opp and opp.slots_filled > 0:
        opp.slots_filled -= 1
        if opp.status == OpportunityStatus.FILLED:
            opp.status = OpportunityStatus.OPEN

    await db.commit()


# ── Hours ───────────────────────────────────────────────────────────


@router.get("/hours/me", response_model=list[VolunteerHoursLogResponse])
async def my_hours(
    user: Annotated[AuthUser, Depends(get_current_user)],
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
):
    """Get my hours history."""
    member_id = await _get_member_id(user, db)
    rows = (
        (
            await db.execute(
                select(VolunteerHoursLog)
                .where(VolunteerHoursLog.member_id == member_id)
                .order_by(VolunteerHoursLog.date.desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.get("/hours/me/summary", response_model=HoursSummaryResponse)
async def my_hours_summary(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Get my hours summary with tier info."""
    member_id = await _get_member_id(user, db)
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Volunteer profile not found")

    # Hours this month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    hours_this_month = (
        await db.execute(
            select(func.coalesce(func.sum(VolunteerHoursLog.hours), 0.0)).where(
                VolunteerHoursLog.member_id == member_id,
                VolunteerHoursLog.created_at >= month_start,
            )
        )
    ).scalar() or 0.0

    # Hours by role
    by_role_rows = (
        await db.execute(
            select(
                VolunteerHoursLog.role_id,
                func.sum(VolunteerHoursLog.hours).label("hours"),
                func.count(VolunteerHoursLog.id).label("count"),
            )
            .where(VolunteerHoursLog.member_id == member_id)
            .group_by(VolunteerHoursLog.role_id)
        )
    ).all()

    by_role = []
    for row in by_role_rows:
        role_id, hours, count = row
        role_name = None
        if role_id:
            role = (
                await db.execute(
                    select(VolunteerRole).where(VolunteerRole.id == role_id)
                )
            ).scalar_one_or_none()
            role_name = role.title if role else None
        by_role.append(
            {
                "role_id": str(role_id) if role_id else None,
                "role_name": role_name,
                "hours": float(hours),
                "sessions": count,
            }
        )

    return HoursSummaryResponse(
        total_hours=profile.total_hours,
        total_sessions=profile.total_sessions_volunteered,
        hours_this_month=float(hours_this_month),
        tier=profile.tier,
        recognition_tier=profile.recognition_tier,
        reliability_score=profile.reliability_score,
        next_tier_hours_needed=next_recognition_hours_needed(profile.total_hours),
        by_role=by_role,
    )


# ── Leaderboard ─────────────────────────────────────────────────────


@router.get("/hours/leaderboard", response_model=list[LeaderboardEntry])
async def leaderboard(
    period: str = Query("all_time", regex="^(all_time|this_month)$"),
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """Top volunteers by hours."""
    if period == "this_month":
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        q = (
            select(
                VolunteerHoursLog.member_id,
                func.sum(VolunteerHoursLog.hours).label("total_hours"),
                func.count(VolunteerHoursLog.id).label("total_sessions"),
            )
            .where(VolunteerHoursLog.created_at >= month_start)
            .group_by(VolunteerHoursLog.member_id)
            .order_by(func.sum(VolunteerHoursLog.hours).desc())
            .limit(limit)
        )
    else:
        q = (
            select(
                VolunteerProfile.member_id,
                VolunteerProfile.total_hours,
                VolunteerProfile.total_sessions_volunteered.label("total_sessions"),
            )
            .where(VolunteerProfile.is_active.is_(True))
            .order_by(VolunteerProfile.total_hours.desc())
            .limit(limit)
        )

    rows = (await db.execute(q)).all()
    results = []
    for rank, row in enumerate(rows, 1):
        member_id = row[0]
        # Get member name
        name_row = await db.execute(
            text("SELECT first_name, last_name FROM members WHERE id = :id"),
            {"id": member_id},
        )
        name = name_row.first()
        member_name = f"{name[0] or ''} {name[1] or ''}".strip() if name else None

        # Get recognition tier
        profile = (
            await db.execute(
                select(VolunteerProfile.recognition_tier).where(
                    VolunteerProfile.member_id == member_id
                )
            )
        ).scalar_one_or_none()

        results.append(
            LeaderboardEntry(
                rank=rank,
                member_id=member_id,
                member_name=member_name,
                total_hours=float(row[1]),
                total_sessions=int(row[2]),
                recognition_tier=profile,
            )
        )
    return results


# ── Rewards ─────────────────────────────────────────────────────────


@router.get("/rewards/me", response_model=list[VolunteerRewardResponse])
async def my_rewards(
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Get my rewards."""
    member_id = await _get_member_id(user, db)
    rows = (
        (
            await db.execute(
                select(VolunteerReward)
                .where(VolunteerReward.member_id == member_id)
                .order_by(VolunteerReward.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post("/rewards/{reward_id}/redeem", response_model=VolunteerRewardResponse)
async def redeem_reward(
    reward_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Redeem a reward."""
    member_id = await _get_member_id(user, db)
    reward = (
        await db.execute(
            select(VolunteerReward).where(
                VolunteerReward.id == reward_id,
                VolunteerReward.member_id == member_id,
            )
        )
    ).scalar_one_or_none()
    if not reward:
        raise HTTPException(status_code=404, detail="Reward not found")
    if reward.is_redeemed:
        raise HTTPException(status_code=400, detail="Reward already redeemed")
    if reward.expires_at and reward.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Reward has expired")

    reward.is_redeemed = True
    reward.redeemed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(reward)
    return reward
