"""Admin: volunteer-opportunity CRUD + bulk-create + publish."""

import secrets
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from services.volunteer_service.models import (
    OpportunityStatus,
    SlotStatus,
    VolunteerOpportunity,
    VolunteerSlot,
)
from services.volunteer_service.schemas import (
    VolunteerOpportunityBulkCreate,
    VolunteerOpportunityCreate,
    VolunteerOpportunityResponse,
    VolunteerOpportunityUpdate,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ._helpers import _enrich_opportunity

router = APIRouter()


@router.get("/opportunities", response_model=list[VolunteerOpportunityResponse])
async def list_opportunities(
    status_filter: Optional[OpportunityStatus] = None,
    role_id: Optional[uuid.UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    skip: int = 0,
    limit: int = 50,
    admin: Annotated[AuthUser, Depends(require_admin)] = None,
    db: AsyncSession = Depends(get_async_db),
):
    """List volunteer opportunities for admin, including drafts by default."""
    q = (
        select(VolunteerOpportunity)
        .options(selectinload(VolunteerOpportunity.role))
        .order_by(
            VolunteerOpportunity.date.asc(), VolunteerOpportunity.created_at.desc()
        )
        .offset(skip)
        .limit(limit)
    )

    if status_filter:
        q = q.where(VolunteerOpportunity.status == status_filter)
    if role_id:
        q = q.where(VolunteerOpportunity.role_id == role_id)
    if from_date:
        q = q.where(VolunteerOpportunity.date >= from_date)
    if to_date:
        q = q.where(VolunteerOpportunity.date <= to_date)

    rows = (await db.execute(q)).scalars().all()
    return [await _enrich_opportunity(opp) for opp in rows]


@router.post(
    "/opportunities", response_model=VolunteerOpportunityResponse, status_code=201
)
async def create_opportunity(
    data: VolunteerOpportunityCreate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    _admin = await get_member_by_auth_id(admin.user_id, calling_service="volunteer")
    admin_member_id = uuid.UUID(_admin["id"]) if _admin else None
    opp = VolunteerOpportunity(**data.model_dump(), created_by=admin_member_id)
    if opp.qr_checkin_enabled:
        opp.qr_token = secrets.token_hex(32)
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
    _admin = await get_member_by_auth_id(admin.user_id, calling_service="volunteer")
    admin_member_id = uuid.UUID(_admin["id"]) if _admin else None
    opps = []
    for item in data.opportunities:
        opp = VolunteerOpportunity(**item.model_dump(), created_by=admin_member_id)
        if opp.qr_checkin_enabled:
            opp.qr_token = secrets.token_hex(32)
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
    # Generate QR token if enabling QR check-in and no token exists yet
    if opp.qr_checkin_enabled and not opp.qr_token:
        opp.qr_token = secrets.token_hex(32)
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
