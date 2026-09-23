"""Service-only Event contracts consumed by Sessions.

Events owns discovery, attendance eligibility, and the shared descriptive/
schedule fields of an Event-backed Session. Sessions owns booking, checkout,
guests, attendance, volunteer operations, and ride-share.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.events_service.models import Event, EventInvite
from services.events_service.routers.member import EventActor, _can_attend_event

router = APIRouter(prefix="/internal/events", tags=["internal-events"])


class EventSessionContract(BaseModel):
    event_id: uuid.UUID
    event_type: str
    status: str
    tier_access: str
    title: str
    description: str | None = None
    starts_at: str
    ends_at: str | None = None
    timezone: str
    pool_id: uuid.UUID | None = None
    location_name: str | None = None
    capacity: int | None = None
    expected_session_count: int | None = None


class EventAttendanceChecksRequest(BaseModel):
    event_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    member_id: uuid.UUID
    paid_tiers: list[Literal["community", "club", "academy"]] = Field(
        default_factory=list
    )


class EventAttendanceDecision(BaseModel):
    allowed: bool
    tier_access: str
    source: str
    reason: str | None = None


@router.get("/{event_id}/session-contract", response_model=EventSessionContract)
async def get_session_contract(
    event_id: uuid.UUID,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    event = await db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return EventSessionContract(
        event_id=event.id,
        event_type=event.event_type,
        status=event.status,
        tier_access=event.tier_access,
        title=event.title,
        description=event.description,
        starts_at=event.start_time.isoformat(),
        ends_at=event.end_time.isoformat() if event.end_time else None,
        timezone=event.timezone,
        pool_id=event.pool_id,
        location_name=event.location,
        capacity=event.max_capacity,
        expected_session_count=1 if event.event_type == "community_swim" else None,
    )


@router.post(
    "/attendance/checks",
    response_model=dict[str, EventAttendanceDecision],
)
async def check_attendance(
    payload: EventAttendanceChecksRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    if not payload.event_ids:
        return {}
    events = list(
        (
            await db.execute(select(Event).where(Event.id.in_(payload.event_ids)))
        ).scalars()
    )
    invites = set(
        (
            await db.execute(
                select(EventInvite.event_id).where(
                    EventInvite.event_id.in_(payload.event_ids),
                    EventInvite.member_id == payload.member_id,
                )
            )
        )
        .scalars()
        .all()
    )
    actor = EventActor(
        member_id=payload.member_id,
        paid_tiers=frozenset(payload.paid_tiers),
        is_authenticated=True,
        is_admin=False,
    )
    decisions: dict[str, EventAttendanceDecision] = {}
    found_ids = {event.id for event in events}
    for event in events:
        invited = event.id in invites
        allowed = _can_attend_event(event, actor, invited=invited)
        source = (
            "event_invitation"
            if allowed and event.tier_access == "invite_only"
            else f"event_{event.tier_access}"
            if allowed
            else "event_policy"
        )
        decisions[str(event.id)] = EventAttendanceDecision(
            allowed=allowed,
            tier_access=event.tier_access,
            source=source,
            reason=None if allowed else "event_access_required",
        )
    for event_id in payload.event_ids:
        if event_id not in found_ids:
            decisions[str(event_id)] = EventAttendanceDecision(
                allowed=False,
                tier_access="event",
                source="event_policy",
                reason="event_unavailable",
            )
    return decisions
