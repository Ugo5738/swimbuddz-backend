"""Events Service router/endpoints."""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.currency import kobo_to_bubbles, naira_to_kobo
from libs.common.service_client import debit_member_wallet
from libs.db.session import get_async_db
from services.events_service.models import Event, EventRSVP, MemberRef
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


async def get_current_member(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> MemberRef:
    """Resolve authenticated user to MemberRef for wallet operations."""
    result = await db.execute(
        select(MemberRef).where(MemberRef.auth_id == current_user.user_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found.",
        )
    return member


def _event_response_dict(event: Event, rsvp_count: dict | None = None) -> dict:
    """Build an EventResponse-compatible dict, converting cost_kobo → cost_naira."""
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "event_type": event.event_type,
        "location": event.location,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "max_capacity": event.max_capacity,
        "tier_access": event.tier_access,
        "cost_naira": (
            (event.cost_kobo / 100.0) if event.cost_kobo is not None else None
        ),
        "created_by": event.created_by,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
        "rsvp_count": rsvp_count or {},
    }


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

        events_with_counts.append(
            EventResponse.model_validate(_event_response_dict(event, rsvp_counts))
        )

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

    return EventResponse.model_validate(_event_response_dict(event, rsvp_counts))


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
    event_dict_in = event_data.model_dump(exclude={"cost_naira"})
    # Convert naira → kobo for DB storage
    cost_naira = event_data.cost_naira
    event_dict_in["cost_kobo"] = (
        naira_to_kobo(cost_naira) if cost_naira is not None else None
    )
    event = Event(**event_dict_in, created_by=created_by)

    db.add(event)
    await db.commit()
    await db.refresh(event)

    return EventResponse.model_validate(_event_response_dict(event))


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

    # Update only provided fields — convert cost_naira → cost_kobo
    update_fields = event_data.model_dump(exclude_unset=True)
    if "cost_naira" in update_fields:
        cost_naira = update_fields.pop("cost_naira")
        update_fields["cost_kobo"] = (
            naira_to_kobo(cost_naira) if cost_naira is not None else None
        )
    for field, value in update_fields.items():
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

    return EventResponse.model_validate(_event_response_dict(event, rsvp_counts))


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
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """Create or update RSVP for an event.

    When pay_with_bubbles=True and status='going', the member's wallet is debited
    for the event fee on the first 'going' RSVP.
    """
    member_id = current_member.id

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

    # Debit wallet on first "going" RSVP when requested and event has a cost
    is_new_going = existing_rsvp is None and rsvp_data.status == "going"
    if (
        is_new_going
        and rsvp_data.pay_with_bubbles
        and event.cost_kobo
        and event.cost_kobo > 0
    ):
        fee_bubbles = kobo_to_bubbles(event.cost_kobo)
        idempotency_key = f"event-{event_id}-{member_id}"
        try:
            await debit_member_wallet(
                current_member.auth_id,
                amount=fee_bubbles,
                idempotency_key=idempotency_key,
                description=f"Event — {event.title} ({fee_bubbles} 🫧)",
                calling_service="events",
                transaction_type="purchase",
                reference_type="event",
                reference_id=str(event_id),
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                detail = e.response.json().get("detail", "")
                if "Insufficient" in detail:
                    raise HTTPException(
                        status_code=402,
                        detail="Insufficient Bubbles. Please top up your wallet.",
                    )
                if "frozen" in detail.lower() or "suspended" in detail.lower():
                    raise HTTPException(
                        status_code=403,
                        detail="Wallet is inactive. Please contact support.",
                    )
            raise

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
