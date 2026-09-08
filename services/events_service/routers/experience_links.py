"""Events retain logistics while a linked Experience owns all admission sales."""

import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.db.session import get_async_db
from services.events_service.models import Event, EventRSVP
from services.events_service.models.experience_operation import (
    ExperienceBindingOperation,
)

router = APIRouter(
    prefix="/internal/events/experiences",
    tags=["internal-experience-events"],
    dependencies=[Depends(require_service_role)],
)


class EventIds(BaseModel):
    event_ids: list[uuid.UUID] = Field(max_length=20)


def event_snapshot(event):
    return {
        "id": str(event.id),
        "title": event.title,
        "description": event.description,
        "start_time": event.start_time.isoformat(),
        "end_time": event.end_time.isoformat() if event.end_time else None,
        "timezone": event.timezone,
        "location": event.location,
        "location_area": event.location_area,
        "is_location_private": event.is_location_private,
        "status": event.status,
        "visibility": event.visibility,
        "max_capacity": event.max_capacity,
        "offering_id": str(event.community_experience_offering_id)
        if event.community_experience_offering_id
        else None,
        "recommended_price_kobo": (event.estimated_cost_per_attendee or 0)
        + (event.margin_amount_per_attendee or 0),
        "cost_lines": event.cost_lines or [],
    }


@router.post("/query")
async def query_events(body: EventIds, db: AsyncSession = Depends(get_async_db)):
    return [
        event_snapshot(event)
        for event in (
            await db.execute(select(Event).where(Event.id.in_(body.event_ids)))
        ).scalars()
    ]


class BindEvents(EventIds):
    offering_id: uuid.UUID
    operation_id: uuid.UUID | None = None
    compensate: bool = False


@router.post("/bind")
async def bind_events(body: BindEvents, db: AsyncSession = Depends(get_async_db)):
    await db.execute(
        select(func.pg_advisory_xact_lock(body.offering_id.int % (2**63 - 1)))
    )
    operation = (
        await db.get(ExperienceBindingOperation, body.operation_id)
        if body.operation_id
        else None
    )
    if operation:
        if operation.offering_id != body.offering_id:
            raise HTTPException(
                409, "Binding operation belongs to a different offering"
            )
        if operation.status == "reverted" and not body.compensate:
            raise HTTPException(
                409, "This binding attempt has already been compensated"
            )
        if not body.compensate and operation.event_ids != sorted(
            str(id) for id in body.event_ids
        ):
            raise HTTPException(409, "Binding operation was used for different Events")
        if (operation.status == "applied" and not body.compensate) or (
            operation.status == "reverted" and body.compensate
        ):
            # A replay must never overwrite a later successful configuration.
            # This also fences duplicate delayed compensation requests.
            return await query_events(EventIds(event_ids=body.event_ids), db)
    rows = list(
        (
            await db.execute(
                select(Event)
                .where(
                    or_(
                        Event.id.in_(body.event_ids),
                        Event.community_experience_offering_id == body.offering_id,
                    )
                )
                .order_by(Event.id)
                .with_for_update()
            )
        ).scalars()
    )
    selected = [event for event in rows if event.id in body.event_ids]
    if len(selected) != len(set(body.event_ids)):
        raise HTTPException(422, "One or more Events do not exist")
    for event in rows:
        if event.id not in body.event_ids:
            event.community_experience_offering_id = None
            continue
        if event.event_type == "open_swim" or event.visibility == "invite_only":
            raise HTTPException(
                422,
                "Use an Admin Event open to members or the public for an Experience",
            )
        if event.community_experience_offering_id not in {None, body.offering_id}:
            raise HTTPException(
                409, "An Event cannot be sold through two Experience offerings"
            )
        if event.community_experience_offering_id is None:
            rsvps = (
                await db.execute(
                    select(func.count(EventRSVP.id)).where(
                        EventRSVP.event_id == event.id
                    )
                )
            ).scalar_one()
            if rsvps:
                raise HTTPException(
                    409,
                    "Resolve existing Event RSVPs/payments before linking this Event",
                )
        event.community_experience_offering_id = body.offering_id
    if body.operation_id:
        if operation is None:
            operation = ExperienceBindingOperation(
                id=body.operation_id,
                offering_id=body.offering_id,
                event_ids=sorted(str(id) for id in body.event_ids),
                status="applied",
            )
            db.add(operation)
        operation.status = "reverted" if body.compensate else "applied"
    await db.commit()
    return [event_snapshot(event) for event in selected]


@router.post("/unbind")
async def unbind_events(body: BindEvents, db: AsyncSession = Depends(get_async_db)):
    rows = (
        await db.execute(
            select(Event)
            .where(Event.id.in_(body.event_ids))
            .order_by(Event.id)
            .with_for_update()
        )
    ).scalars()
    for event in rows:
        if event.community_experience_offering_id == body.offering_id:
            event.community_experience_offering_id = None
    await db.commit()
    return {"unlinked": True}
