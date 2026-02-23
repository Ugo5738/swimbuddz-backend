"""Events Service router/endpoints."""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.events_service.models import Event, EventRSVP
from services.events_service.schemas import (
    EventCreate,
    EventResponse,
    EventUpdate,
    RSVPCreate,
    RSVPResponse,
)
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/", response_model=List[EventResponse])
async def list_events(
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    upcoming_only: bool = Query(True, description="Show only upcoming events"),
    db: AsyncSession = Depends(get_async_db),
):
    """List all events with optional filters."""
    query = select(Event)

    if event_type:
        query = query.where(Event.event_type == event_type)

    if upcoming_only:
        query = query.where(Event.start_time >= datetime.now(timezone.utc))

    query = query.order_by(Event.start_time.asc())

    result = await db.execute(query)
    events = result.scalars().all()

    # Get RSVP counts for each event
    events_with_counts = []
    for event in events:
        rsvp_query = (
            select(EventRSVP.status, func.count(EventRSVP.id).label("count"))
            .where(EventRSVP.event_id == event.id)
            .group_by(EventRSVP.status)
        )

        rsvp_result = await db.execute(rsvp_query)
        rsvp_counts = {row[0]: row[1] for row in rsvp_result.all()}

        event_dict = event.__dict__.copy()
        event_dict["rsvp_count"] = rsvp_counts
        events_with_counts.append(EventResponse.model_validate(event_dict))

    return events_with_counts


@router.delete("/admin/members/{member_id}")
async def admin_delete_member_event_rsvps(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete event RSVPs for a member (Admin only).
    """
    result = await db.execute(delete(EventRSVP).where(EventRSVP.member_id == member_id))
    await db.commit()
    return {"deleted": result.rowcount or 0}


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single event by ID."""
    query = select(Event).where(Event.id == event_id)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Get RSVP counts
    rsvp_query = (
        select(EventRSVP.status, func.count(EventRSVP.id).label("count"))
        .where(EventRSVP.event_id == event.id)
        .group_by(EventRSVP.status)
    )

    rsvp_result = await db.execute(rsvp_query)
    rsvp_counts = {row[0]: row[1] for row in rsvp_result.all()}

    event_dict = event.__dict__.copy()
    event_dict["rsvp_count"] = rsvp_counts

    return EventResponse.model_validate(event_dict)


@router.post("/", response_model=EventResponse, status_code=201)
async def create_event(
    event_data: EventCreate,
    # TODO: Add authentication to get current user ID
    # For now, we'll require created_by to be passed in the request body
    created_by: uuid.UUID = Query(
        ..., description="Admin member ID creating the event"
    ),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new event (admin only)."""
    event = Event(**event_data.model_dump(), created_by=created_by)

    db.add(event)
    await db.commit()
    await db.refresh(event)

    event_dict = event.__dict__.copy()
    event_dict["rsvp_count"] = {}

    return EventResponse.model_validate(event_dict)


@router.patch("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: uuid.UUID,
    event_data: EventUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update an event (admin only)."""
    query = select(Event).where(Event.id == event_id)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Update only provided fields
    for field, value in event_data.model_dump(exclude_unset=True).items():
        setattr(event, field, value)

    await db.commit()
    await db.refresh(event)

    # Get RSVP counts
    rsvp_query = (
        select(EventRSVP.status, func.count(EventRSVP.id).label("count"))
        .where(EventRSVP.event_id == event.id)
        .group_by(EventRSVP.status)
    )

    rsvp_result = await db.execute(rsvp_query)
    rsvp_counts = {row[0]: row[1] for row in rsvp_result.all()}

    event_dict = event.__dict__.copy()
    event_dict["rsvp_count"] = rsvp_counts

    return EventResponse.model_validate(event_dict)


@router.delete("/{event_id}", status_code=204)
async def delete_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Delete an event (admin only)."""
    query = select(Event).where(Event.id == event_id)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Delete associated RSVPs first
    await db.execute(select(EventRSVP).where(EventRSVP.event_id == event_id))
    await db.delete(event)
    await db.commit()

    return None


@router.post("/{event_id}/rsvp", response_model=RSVPResponse)
async def create_or_update_rsvp(
    event_id: uuid.UUID,
    rsvp_data: RSVPCreate,
    # TODO: Get member_id from authentication
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """Create or update RSVP for an event."""
    # Check if event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Check if RSVP already exists
    rsvp_query = select(EventRSVP).where(
        and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member_id)
    )
    rsvp_result = await db.execute(rsvp_query)
    existing_rsvp = rsvp_result.scalar_one_or_none()

    if existing_rsvp:
        # Update existing RSVP
        existing_rsvp.status = rsvp_data.status
        existing_rsvp.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(existing_rsvp)
        return RSVPResponse.model_validate(existing_rsvp)
    else:
        # Create new RSVP
        rsvp = EventRSVP(
            event_id=event_id, member_id=member_id, status=rsvp_data.status
        )
        db.add(rsvp)
        await db.commit()
        await db.refresh(rsvp)
        return RSVPResponse.model_validate(rsvp)


@router.get("/{event_id}/rsvps", response_model=List[RSVPResponse])
async def list_event_rsvps(
    event_id: uuid.UUID,
    status: Optional[str] = Query(None, description="Filter by RSVP status"),
    db: AsyncSession = Depends(get_async_db),
):
    """List all RSVPs for an event (admin only)."""
    query = select(EventRSVP).where(EventRSVP.event_id == event_id)

    if status:
        query = query.where(EventRSVP.status == status)

    result = await db.execute(query)
    rsvps = result.scalars().all()

    return [RSVPResponse.model_validate(rsvp) for rsvp in rsvps]
