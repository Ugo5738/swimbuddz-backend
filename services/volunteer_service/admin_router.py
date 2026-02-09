"""Admin volunteer management endpoints."""

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.member_utils import resolve_members_basic
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
    BulkCompleteRequest,
    CheckoutSlotRequest,
    FeatureVolunteerRequest,
    LeaderboardEntry,
    ManualHoursCreate,
    VolunteerDashboardSummary,
    VolunteerHoursLogResponse,
    VolunteerOpportunityBulkCreate,
    VolunteerOpportunityCreate,
    VolunteerOpportunityResponse,
    VolunteerOpportunityUpdate,
    VolunteerProfileAdminUpdate,
    VolunteerProfileResponse,
    VolunteerRewardCreate,
    VolunteerRewardResponse,
    VolunteerRoleCreate,
    VolunteerRoleResponse,
    VolunteerRoleUpdate,
    VolunteerSlotAdminUpdate,
    VolunteerSlotResponse,
)
from services.volunteer_service.services import (
    compute_reliability_score,
    update_profile_aggregates,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/admin/volunteers", tags=["admin-volunteers"])


# ── Helpers ─────────────────────────────────────────────────────────


async def _get_admin_member_id(user: AuthUser, db: AsyncSession) -> uuid.UUID | None:
    """Resolve admin auth_id → member UUID (may be None for service roles)."""
    row = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": user.user_id},
    )
    return row.scalar_one_or_none()


async def _enrich_opportunity(opp: VolunteerOpportunity) -> dict:
    data = {c.key: getattr(opp, c.key) for c in opp.__table__.columns}
    data["role_title"] = opp.role.title if opp.role else None
    data["role_category"] = opp.role.category.value if opp.role else None
    return data


async def _enrich_slot(slot: VolunteerSlot, db: AsyncSession) -> dict:
    data = {c.key: getattr(slot, c.key) for c in slot.__table__.columns}
    name_row = await db.execute(
        text("SELECT first_name, last_name FROM members WHERE id = :id"),
        {"id": slot.member_id},
    )
    name = name_row.first()
    data["member_name"] = f"{name[0] or ''} {name[1] or ''}".strip() if name else None
    return data


# ── Roles CRUD ──────────────────────────────────────────────────────


@router.post("/roles", response_model=VolunteerRoleResponse, status_code=201)
async def create_role(
    data: VolunteerRoleCreate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    role = VolunteerRole(**data.model_dump())
    db.add(role)
    await db.commit()
    await db.refresh(role)
    result = {c.key: getattr(role, c.key) for c in role.__table__.columns}
    result["active_volunteers_count"] = 0
    return result


@router.patch("/roles/{role_id}", response_model=VolunteerRoleResponse)
async def update_role(
    role_id: uuid.UUID,
    data: VolunteerRoleUpdate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    role = (
        await db.execute(select(VolunteerRole).where(VolunteerRole.id == role_id))
    ).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(role, field, value)
    await db.commit()
    await db.refresh(role)
    result = {c.key: getattr(role, c.key) for c in role.__table__.columns}
    result["active_volunteers_count"] = 0
    return result


@router.delete("/roles/{role_id}", status_code=204)
async def deactivate_role(
    role_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    role = (
        await db.execute(select(VolunteerRole).where(VolunteerRole.id == role_id))
    ).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    role.is_active = False
    await db.commit()


# ── Profiles ────────────────────────────────────────────────────────


@router.get("/profiles", response_model=list[VolunteerProfileResponse])
async def list_profiles(
    tier: Optional[VolunteerTier] = None,
    active_only: bool = True,
    skip: int = 0,
    limit: int = 50,
    admin: Annotated[AuthUser, Depends(require_admin)] = None,
    db: AsyncSession = Depends(get_async_db),
):
    q = select(VolunteerProfile).offset(skip).limit(limit)
    if tier:
        q = q.where(VolunteerProfile.tier == tier)
    if active_only:
        q = q.where(VolunteerProfile.is_active.is_(True))
    q = q.order_by(VolunteerProfile.total_hours.desc())

    profiles = (await db.execute(q)).scalars().all()
    results = []
    for p in profiles:
        data = {c.key: getattr(p, c.key) for c in p.__table__.columns}
        name_row = await db.execute(
            text("SELECT first_name, last_name, email FROM members WHERE id = :id"),
            {"id": p.member_id},
        )
        name = name_row.first()
        data["member_name"] = (
            f"{name[0] or ''} {name[1] or ''}".strip() if name else None
        )
        data["member_email"] = name[2] if name else None
        results.append(data)
    return results


@router.get("/profiles/{member_id}", response_model=VolunteerProfileResponse)
async def get_profile(
    member_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    data = {c.key: getattr(profile, c.key) for c in profile.__table__.columns}
    name_row = await db.execute(
        text("SELECT first_name, last_name, email FROM members WHERE id = :id"),
        {"id": member_id},
    )
    name = name_row.first()
    data["member_name"] = f"{name[0] or ''} {name[1] or ''}".strip() if name else None
    data["member_email"] = name[2] if name else None
    return data


@router.patch("/profiles/{member_id}", response_model=VolunteerProfileResponse)
async def admin_update_profile(
    member_id: uuid.UUID,
    data: VolunteerProfileAdminUpdate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return profile


@router.post(
    "/profiles/{member_id}/feature",
    response_model=VolunteerProfileResponse,
)
async def feature_volunteer(
    member_id: uuid.UUID,
    data: FeatureVolunteerRequest,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    """Feature a volunteer for the public spotlight. Un-features any currently featured volunteer."""
    # Un-feature all currently featured
    current_featured = (
        (
            await db.execute(
                select(VolunteerProfile).where(VolunteerProfile.is_featured.is_(True))
            )
        )
        .scalars()
        .all()
    )
    for p in current_featured:
        p.is_featured = False

    # Feature the target
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile.is_featured = True
    profile.featured_from = datetime.now(timezone.utc)
    profile.featured_until = data.featured_until
    if data.spotlight_quote is not None:
        profile.spotlight_quote = data.spotlight_quote

    await db.commit()
    await db.refresh(profile)

    result = {c.key: getattr(profile, c.key) for c in profile.__table__.columns}
    member_info = await resolve_members_basic([member_id])
    info = member_info.get(str(member_id))
    result["member_name"] = info.full_name if info else None
    result["member_email"] = info.email if info else None
    return result


@router.delete("/profiles/{member_id}/feature", status_code=204)
async def unfeature_volunteer(
    member_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    """Remove a volunteer from the spotlight."""
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    profile.is_featured = False
    await db.commit()


# ── Opportunities ───────────────────────────────────────────────────


@router.post(
    "/opportunities", response_model=VolunteerOpportunityResponse, status_code=201
)
async def create_opportunity(
    data: VolunteerOpportunityCreate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    admin_member_id = await _get_admin_member_id(admin, db)
    opp = VolunteerOpportunity(**data.model_dump(), created_by=admin_member_id)
    db.add(opp)
    await db.commit()

    # Reload with role
    opp = (
        await db.execute(
            select(VolunteerOpportunity)
            .options(selectinload(VolunteerOpportunity.role))
            .where(VolunteerOpportunity.id == opp.id)
        )
    ).scalar_one()
    return await _enrich_opportunity(opp)


@router.post(
    "/opportunities/bulk",
    response_model=list[VolunteerOpportunityResponse],
    status_code=201,
)
async def bulk_create_opportunities(
    data: VolunteerOpportunityBulkCreate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    admin_member_id = await _get_admin_member_id(admin, db)
    opps = []
    for item in data.opportunities:
        opp = VolunteerOpportunity(**item.model_dump(), created_by=admin_member_id)
        db.add(opp)
        opps.append(opp)
    await db.commit()

    results = []
    for opp in opps:
        await db.refresh(opp)
        loaded = (
            await db.execute(
                select(VolunteerOpportunity)
                .options(selectinload(VolunteerOpportunity.role))
                .where(VolunteerOpportunity.id == opp.id)
            )
        ).scalar_one()
        results.append(await _enrich_opportunity(loaded))
    return results


@router.patch("/opportunities/{opp_id}", response_model=VolunteerOpportunityResponse)
async def update_opportunity(
    opp_id: uuid.UUID,
    data: VolunteerOpportunityUpdate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    opp = (
        await db.execute(
            select(VolunteerOpportunity)
            .options(selectinload(VolunteerOpportunity.role))
            .where(VolunteerOpportunity.id == opp_id)
        )
    ).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(opp, field, value)
    await db.commit()
    await db.refresh(opp)
    # Re-load with role
    opp = (
        await db.execute(
            select(VolunteerOpportunity)
            .options(selectinload(VolunteerOpportunity.role))
            .where(VolunteerOpportunity.id == opp.id)
        )
    ).scalar_one()
    return await _enrich_opportunity(opp)


@router.delete("/opportunities/{opp_id}", status_code=204)
async def cancel_opportunity(
    opp_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    opp = (
        await db.execute(
            select(VolunteerOpportunity).where(VolunteerOpportunity.id == opp_id)
        )
    ).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    # Cancel all active slots
    active_slots = (
        (
            await db.execute(
                select(VolunteerSlot).where(
                    VolunteerSlot.opportunity_id == opp_id,
                    VolunteerSlot.status.in_([SlotStatus.CLAIMED, SlotStatus.APPROVED]),
                )
            )
        )
        .scalars()
        .all()
    )
    for slot in active_slots:
        slot.status = SlotStatus.CANCELLED
        slot.cancelled_at = datetime.now(timezone.utc)
        slot.cancellation_reason = "Opportunity cancelled by admin"

    opp.status = OpportunityStatus.CANCELLED
    await db.commit()


@router.post(
    "/opportunities/{opp_id}/publish", response_model=VolunteerOpportunityResponse
)
async def publish_opportunity(
    opp_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    opp = (
        await db.execute(
            select(VolunteerOpportunity)
            .options(selectinload(VolunteerOpportunity.role))
            .where(VolunteerOpportunity.id == opp_id)
        )
    ).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if opp.status != OpportunityStatus.DRAFT:
        raise HTTPException(
            status_code=400, detail="Only draft opportunities can be published"
        )
    opp.status = OpportunityStatus.OPEN
    await db.commit()
    await db.refresh(opp)
    return await _enrich_opportunity(opp)


# ── Slot Management ─────────────────────────────────────────────────


@router.get("/opportunities/{opp_id}/slots", response_model=list[VolunteerSlotResponse])
async def list_slots(
    opp_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    rows = (
        (
            await db.execute(
                select(VolunteerSlot)
                .where(VolunteerSlot.opportunity_id == opp_id)
                .order_by(VolunteerSlot.claimed_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [await _enrich_slot(s, db) for s in rows]


@router.patch("/slots/{slot_id}", response_model=VolunteerSlotResponse)
async def update_slot(
    slot_id: uuid.UUID,
    data: VolunteerSlotAdminUpdate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    slot = (
        await db.execute(select(VolunteerSlot).where(VolunteerSlot.id == slot_id))
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    if data.status == SlotStatus.APPROVED:
        slot.status = SlotStatus.APPROVED
        slot.approved_at = datetime.now(timezone.utc)
        admin_member_id = await _get_admin_member_id(admin, db)
        slot.approved_by = admin_member_id
    elif data.status == SlotStatus.REJECTED:
        slot.status = SlotStatus.REJECTED
        # Decrement filled count
        opp = (
            await db.execute(
                select(VolunteerOpportunity).where(
                    VolunteerOpportunity.id == slot.opportunity_id
                )
            )
        ).scalar_one_or_none()
        if opp and opp.slots_filled > 0:
            opp.slots_filled -= 1
    elif data.status:
        slot.status = data.status

    if data.admin_notes is not None:
        slot.admin_notes = data.admin_notes

    await db.commit()
    await db.refresh(slot)
    return await _enrich_slot(slot, db)


@router.post("/slots/{slot_id}/checkin", response_model=VolunteerSlotResponse)
async def checkin_slot(
    slot_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    slot = (
        await db.execute(select(VolunteerSlot).where(VolunteerSlot.id == slot_id))
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    if slot.status not in (SlotStatus.CLAIMED, SlotStatus.APPROVED):
        raise HTTPException(
            status_code=400, detail="Slot must be claimed or approved to check in"
        )
    slot.checked_in_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(slot)
    return await _enrich_slot(slot, db)


@router.post("/slots/{slot_id}/checkout", response_model=VolunteerSlotResponse)
async def checkout_slot(
    slot_id: uuid.UUID,
    data: CheckoutSlotRequest = None,
    admin: Annotated[AuthUser, Depends(require_admin)] = None,
    db: AsyncSession = Depends(get_async_db),
):
    slot = (
        await db.execute(select(VolunteerSlot).where(VolunteerSlot.id == slot_id))
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    if not slot.checked_in_at:
        raise HTTPException(status_code=400, detail="Must check in before checking out")

    now = datetime.now(timezone.utc)
    slot.checked_out_at = now
    slot.status = SlotStatus.COMPLETED

    # Calculate hours
    if data and data.hours:
        slot.hours_logged = data.hours
    else:
        delta = now - slot.checked_in_at
        slot.hours_logged = round(delta.total_seconds() / 3600, 2)

    if data and data.admin_notes:
        slot.admin_notes = data.admin_notes

    # Create hours log entry
    opp = (
        await db.execute(
            select(VolunteerOpportunity).where(
                VolunteerOpportunity.id == slot.opportunity_id
            )
        )
    ).scalar_one_or_none()

    hours_log = VolunteerHoursLog(
        member_id=slot.member_id,
        slot_id=slot.id,
        opportunity_id=slot.opportunity_id,
        hours=slot.hours_logged,
        date=opp.date if opp else date.today(),
        role_id=opp.role_id if opp else None,
        source="slot_completion",
        logged_by=await _get_admin_member_id(admin, db) if admin else None,
    )
    db.add(hours_log)

    await db.commit()

    # Update profile aggregates
    await update_profile_aggregates(db, slot.member_id)
    await db.commit()

    await db.refresh(slot)
    return await _enrich_slot(slot, db)


@router.post("/slots/{slot_id}/no-show", response_model=VolunteerSlotResponse)
async def mark_no_show(
    slot_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    slot = (
        await db.execute(select(VolunteerSlot).where(VolunteerSlot.id == slot_id))
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    slot.status = SlotStatus.NO_SHOW

    # Update profile
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == slot.member_id)
        )
    ).scalar_one_or_none()
    if profile:
        profile.total_no_shows += 1
        profile.reliability_score = compute_reliability_score(
            profile.total_no_shows, profile.total_late_cancellations
        )

    await db.commit()
    await db.refresh(slot)
    return await _enrich_slot(slot, db)


@router.post("/slots/bulk-complete", response_model=list[VolunteerSlotResponse])
async def bulk_complete(
    data: BulkCompleteRequest,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    results = []
    admin_member_id = await _get_admin_member_id(admin, db)
    for slot_id in data.slot_ids:
        slot = (
            await db.execute(select(VolunteerSlot).where(VolunteerSlot.id == slot_id))
        ).scalar_one_or_none()
        if not slot:
            continue

        now = datetime.now(timezone.utc)
        slot.checked_out_at = now
        slot.status = SlotStatus.COMPLETED
        slot.hours_logged = data.hours or 2.0  # Default 2 hours if not specified

        opp = (
            await db.execute(
                select(VolunteerOpportunity).where(
                    VolunteerOpportunity.id == slot.opportunity_id
                )
            )
        ).scalar_one_or_none()

        hours_log = VolunteerHoursLog(
            member_id=slot.member_id,
            slot_id=slot.id,
            opportunity_id=slot.opportunity_id,
            hours=slot.hours_logged,
            date=opp.date if opp else date.today(),
            role_id=opp.role_id if opp else None,
            source="slot_completion",
            logged_by=admin_member_id,
        )
        db.add(hours_log)
        results.append(slot)

    await db.commit()

    # Update aggregates for each member
    member_ids = {s.member_id for s in results}
    for mid in member_ids:
        await update_profile_aggregates(db, mid)
    await db.commit()

    enriched = []
    for slot in results:
        await db.refresh(slot)
        enriched.append(await _enrich_slot(slot, db))
    return enriched


# ── Manual Hours ────────────────────────────────────────────────────


@router.post("/hours/manual", response_model=VolunteerHoursLogResponse, status_code=201)
async def add_manual_hours(
    data: ManualHoursCreate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    admin_member_id = await _get_admin_member_id(admin, db)
    log = VolunteerHoursLog(
        member_id=data.member_id,
        hours=data.hours,
        date=data.date,
        role_id=data.role_id,
        source="manual_entry",
        logged_by=admin_member_id,
        notes=data.notes,
    )
    db.add(log)
    await db.commit()

    # Update profile aggregates
    await update_profile_aggregates(db, data.member_id)
    await db.commit()

    await db.refresh(log)
    return log


# ── Rewards ─────────────────────────────────────────────────────────


@router.post("/rewards", response_model=VolunteerRewardResponse, status_code=201)
async def grant_reward(
    data: VolunteerRewardCreate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    admin_member_id = await _get_admin_member_id(admin, db)
    reward = VolunteerReward(
        **data.model_dump(),
        granted_by=admin_member_id,
    )
    db.add(reward)
    await db.commit()
    await db.refresh(reward)
    return reward


@router.get("/rewards/all", response_model=list[VolunteerRewardResponse])
async def list_all_rewards(
    admin: Annotated[AuthUser, Depends(require_admin)],
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
):
    rows = (
        (
            await db.execute(
                select(VolunteerReward)
                .order_by(VolunteerReward.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return rows


# ── Dashboard ───────────────────────────────────────────────────────


@router.get("/dashboard", response_model=VolunteerDashboardSummary)
async def admin_dashboard(
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    # Total active volunteers
    total_active = (
        await db.execute(
            select(func.count(VolunteerProfile.id)).where(
                VolunteerProfile.is_active.is_(True)
            )
        )
    ).scalar() or 0

    # Hours this month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    hours_this_month = (
        await db.execute(
            select(func.coalesce(func.sum(VolunteerHoursLog.hours), 0.0)).where(
                VolunteerHoursLog.created_at >= month_start
            )
        )
    ).scalar() or 0.0

    # Upcoming opportunities (next 14 days)
    today = date.today()
    upcoming = (
        await db.execute(
            select(func.count(VolunteerOpportunity.id)).where(
                VolunteerOpportunity.date >= today,
                VolunteerOpportunity.date <= today + timedelta(days=14),
                VolunteerOpportunity.status.in_(
                    [
                        OpportunityStatus.OPEN,
                        OpportunityStatus.IN_PROGRESS,
                    ]
                ),
            )
        )
    ).scalar() or 0

    # Unfilled slots
    unfilled = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        VolunteerOpportunity.slots_needed
                        - VolunteerOpportunity.slots_filled
                    ),
                    0,
                )
            ).where(
                VolunteerOpportunity.status == OpportunityStatus.OPEN,
                VolunteerOpportunity.date >= today,
            )
        )
    ).scalar() or 0

    # No-show rate
    total_completed = (
        await db.execute(
            select(func.count(VolunteerSlot.id)).where(
                VolunteerSlot.status == SlotStatus.COMPLETED
            )
        )
    ).scalar() or 0
    total_no_shows = (
        await db.execute(
            select(func.count(VolunteerSlot.id)).where(
                VolunteerSlot.status == SlotStatus.NO_SHOW
            )
        )
    ).scalar() or 0
    total_events = total_completed + total_no_shows
    no_show_rate = (total_no_shows / total_events * 100) if total_events > 0 else 0.0

    # Top 5 volunteers
    top_rows = (
        (
            await db.execute(
                select(VolunteerProfile)
                .where(VolunteerProfile.is_active.is_(True))
                .order_by(VolunteerProfile.total_hours.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )

    top_volunteers = []
    for rank, p in enumerate(top_rows, 1):
        name_row = await db.execute(
            text("SELECT first_name, last_name FROM members WHERE id = :id"),
            {"id": p.member_id},
        )
        name = name_row.first()
        top_volunteers.append(
            LeaderboardEntry(
                rank=rank,
                member_id=p.member_id,
                member_name=(
                    f"{name[0] or ''} {name[1] or ''}".strip() if name else None
                ),
                total_hours=p.total_hours,
                total_sessions=p.total_sessions_volunteered,
                recognition_tier=p.recognition_tier,
            )
        )

    return VolunteerDashboardSummary(
        total_active_volunteers=total_active,
        total_hours_this_month=float(hours_this_month),
        upcoming_opportunities=upcoming,
        unfilled_slots=unfilled,
        no_show_rate=round(no_show_rate, 1),
        top_volunteers=top_volunteers,
    )


@router.get("/reliability-report", response_model=list[VolunteerProfileResponse])
async def reliability_report(
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    """Volunteer reliability report, sorted ascending (worst first)."""
    profiles = (
        (
            await db.execute(
                select(VolunteerProfile)
                .where(VolunteerProfile.is_active.is_(True))
                .order_by(VolunteerProfile.reliability_score.asc())
            )
        )
        .scalars()
        .all()
    )

    results = []
    for p in profiles:
        data = {c.key: getattr(p, c.key) for c in p.__table__.columns}
        name_row = await db.execute(
            text("SELECT first_name, last_name, email FROM members WHERE id = :id"),
            {"id": p.member_id},
        )
        name = name_row.first()
        data["member_name"] = (
            f"{name[0] or ''} {name[1] or ''}".strip() if name else None
        )
        data["member_email"] = name[2] if name else None
        results.append(data)
    return results
