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
    visibility: str = "public"
    is_location_private: bool = False
    location_area: str | None = None
    capacity: int | None = None
    expected_session_count: int | None = None


class EventAttendanceCheck(BaseModel):
    context_key: str = Field(min_length=1, max_length=100)
    event_id: uuid.UUID
    paid_tiers: list[Literal["community", "club", "academy"]] = Field(
        default_factory=list
    )


class EventAttendanceChecksRequest(BaseModel):
    checks: list[EventAttendanceCheck] = Field(default_factory=list, max_length=200)
    member_id: uuid.UUID


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
        visibility=event.visibility,
        is_location_private=event.is_location_private,
        location_area=event.location_area,
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
    if not payload.checks:
        return {}
    event_ids = list(dict.fromkeys(check.event_id for check in payload.checks))
    events = list(
        (await db.execute(select(Event).where(Event.id.in_(event_ids)))).scalars()
    )
    invites = set(
        (
            await db.execute(
                select(EventInvite.event_id).where(
                    EventInvite.event_id.in_(event_ids),
                    EventInvite.member_id == payload.member_id,
                )
            )
        )
        .scalars()
        .all()
    )
    events_by_id = {event.id: event for event in events}
    decisions: dict[str, EventAttendanceDecision] = {}
    for check in payload.checks:
        event = events_by_id.get(check.event_id)
        if event is None:
            decisions[check.context_key] = EventAttendanceDecision(
                allowed=False,
                tier_access="event",
                source="event_policy",
                reason="event_unavailable",
            )
            continue
        actor = EventActor(
            member_id=payload.member_id,
            paid_tiers=frozenset(check.paid_tiers),
            is_authenticated=True,
            is_admin=False,
        )
        invited = event.id in invites
        allowed = _can_attend_event(event, actor, invited=invited)
        source = (
            "event_invitation"
            if allowed and event.tier_access == "invite_only"
            else f"event_{event.tier_access}"
            if allowed
            else "event_policy"
        )
        decisions[check.context_key] = EventAttendanceDecision(
            allowed=allowed,
            tier_access=event.tier_access,
            source=source,
            reason=None if allowed else "event_access_required",
        )
    return decisions
