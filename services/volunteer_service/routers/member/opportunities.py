"""Volunteer opportunity listing endpoints.

Route ordering: `/opportunities/upcoming` is registered before
`/opportunities/{opp_id}` so FastAPI doesn't capture the literal segment
"upcoming" as a UUID.
"""

import uuid
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.db.session import get_async_db
from services.volunteer_service.models import OpportunityStatus, VolunteerOpportunity
from services.volunteer_service.schemas import VolunteerOpportunityResponse

from ._helpers import _enrich_opportunity

router = APIRouter()


@router.get("/opportunities", response_model=list[VolunteerOpportunityResponse])
async def list_opportunities(
    status_filter: Optional[OpportunityStatus] = Query(None, alias="status"),
    role_id: Optional[uuid.UUID] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    session_id: Optional[uuid.UUID] = None,
    event_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
):
    """List volunteer opportunities (open ones visible to all authenticated members).

    Optional ``session_id`` / ``event_id`` filters return only opportunities
    attached to the given session or event — used by the booking and event
    detail pages to surface "claim a volunteer slot at this session" CTAs.
    """
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
    if session_id:
        q = q.where(VolunteerOpportunity.session_id == session_id)
    if event_id:
        q = q.where(VolunteerOpportunity.event_id == event_id)

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
