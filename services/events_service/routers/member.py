"""Events Service router/endpoints."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import (
    get_current_user,
    get_optional_user,
    is_admin_or_service,
    require_admin,
)
from libs.auth.models import AuthUser
from libs.common.currency import kobo_to_bubbles, kobo_to_bubbles_exact, naira_to_kobo
from libs.common.datetime_utils import utc_now
from libs.common.service_client import (
    credit_member_wallet,
    debit_member_wallet,
    get_member_by_id,
    get_member_membership,
    get_members_bulk,
    get_partner_pool,
)
from libs.common.session_access import active_paid_tiers
from libs.db.session import get_async_db
from services.events_service.models import Event, EventInvite, EventRSVP, MemberRef
from services.events_service.schemas import (
    EventCreate,
    EventInviteCreate,
    EventInviteResponse,
    EventResponse,
    EventUpdate,
    OpenSwimCreate,
    OpenSwimUpdate,
    RSVPCreate,
    RSVPResponse,
)
from services.events_service.services.chat_sync import (
    ensure_event_channel,
    reconcile_event_membership,
)
from services.events_service.services.pricing import (
    PRICING_KEYS,
    event_pricing_payload,
    event_pricing_response,
    normalize_event_pricing,
)

router = APIRouter(prefix="/events", tags=["events"])


@dataclass(frozen=True)
class EventActor:
    """Server-owned visibility context for an event viewer."""

    member_id: Optional[uuid.UUID]
    paid_tiers: frozenset[str]
    is_authenticated: bool
    is_admin: bool


async def _resolve_event_actor(
    current_user: Optional[AuthUser],
    db: AsyncSession,
) -> EventActor:
    if current_user is None:
        return EventActor(None, frozenset(), False, False)
    if is_admin_or_service(current_user):
        return EventActor(None, frozenset(), True, True)

    member = (
        await db.execute(
            select(MemberRef).where(MemberRef.auth_id == current_user.user_id)
        )
    ).scalar_one_or_none()
    if member is None:
        return EventActor(None, frozenset(), True, False)

    membership = await get_member_membership(
        str(member.id),
        calling_service="events",
    )
    return EventActor(
        member_id=member.id,
        paid_tiers=frozenset(active_paid_tiers(membership or {})),
        is_authenticated=True,
        is_admin=False,
    )


def _can_view_event(event: Event, actor: EventActor, *, invited: bool) -> bool:
    if actor.is_admin:
        return True
    if event.status != "published":
        return False
    if event.visibility == "public":
        return True
    if not actor.is_authenticated:
        return False
    if event.visibility == "invite_only":
        return invited
    if event.tier_access == "public":
        return True
    return event.tier_access in ({"community"} | set(actor.paid_tiers))


def _can_attend_event(event: Event, actor: EventActor, *, invited: bool) -> bool:
    if actor.is_admin:
        return True
    if event.status != "published" or not actor.is_authenticated:
        return False
    if event.tier_access == "public":
        return True
    if event.tier_access == "invite_only":
        return invited
    return event.tier_access in ({"community"} | set(actor.paid_tiers))


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


def _total_charge_kobo(event: Event) -> int:
    """Effective per-attendee charge in kobo.

    Admin events use ``cost_kobo``; member open-swims use
    ``pool_fee_kobo + organizer_surcharge_kobo``. The unused side is NULL/0, so
    summing all three is safe.
    """
    return (
        (event.cost_kobo or 0)
        + (event.pool_fee_kobo or 0)
        + (event.organizer_surcharge_kobo or 0)
    )


def _event_response_dict(
    event: Event,
    rsvp_count: dict | None = None,
    *,
    actor: Optional[EventActor] = None,
    invited: bool = False,
) -> dict:
    """Build an EventResponse-compatible dict, converting kobo → naira."""
    total_kobo = _total_charge_kobo(event)
    viewer_can_attend = bool(actor and _can_attend_event(event, actor, invited=invited))
    hide_location = bool(event.is_location_private and not viewer_can_attend)
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "event_type": event.event_type,
        "audience": event.audience,
        "visibility": event.visibility,
        "status": event.status,
        "location_type": event.location_type,
        "timezone": event.timezone,
        "location_area": event.location_area,
        "is_location_private": event.is_location_private,
        "location": "Venue shared after RSVP" if hide_location else event.location,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "max_capacity": event.max_capacity,
        "tier_access": event.tier_access,
        **event_pricing_response(event),
        "email_reminder_hours": event.email_reminder_hours or [],
        "pool_id": None if hide_location else event.pool_id,
        "pool_fee_naira": (
            (event.pool_fee_kobo / 100.0) if event.pool_fee_kobo is not None else None
        ),
        "organizer_surcharge_naira": (
            (event.organizer_surcharge_kobo / 100.0)
            if event.organizer_surcharge_kobo is not None
            else None
        ),
        "total_cost_naira": (total_kobo / 100.0) if total_kobo > 0 else None,
        "created_by": event.created_by,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
        "rsvp_count": rsvp_count or {},
        "viewer_can_attend": viewer_can_attend,
        "viewer_invited": invited,
    }


@router.get("/", response_model=List[EventResponse])
async def list_events(
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    upcoming_only: bool = Query(True, description="Show only upcoming events"),
    audience: Optional[str] = Query(None),
    visibility: Optional[str] = Query(None),
    location_type: Optional[str] = Query(None),
    event_status: Optional[str] = Query(None, alias="status"),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List events visible to the current visitor, member, or admin."""
    actor = await _resolve_event_actor(current_user, db)
    query = select(Event)

    if event_type:
        query = query.where(Event.event_type == event_type)
    if audience:
        query = query.where(Event.audience == audience)
    if visibility:
        query = query.where(Event.visibility == visibility)
    if location_type:
        query = query.where(Event.location_type == location_type)
    if event_status:
        query = query.where(Event.status == event_status)

    if upcoming_only:
        query = query.where(Event.start_time >= utc_now())

    query = query.order_by(Event.start_time.asc())

    result = await db.execute(query)
    events = result.scalars().all()
    invited_event_ids: set[uuid.UUID] = set()
    if actor.member_id and events:
        invited_event_ids = set(
            (
                await db.execute(
                    select(EventInvite.event_id).where(
                        EventInvite.member_id == actor.member_id,
                        EventInvite.event_id.in_([event.id for event in events]),
                    )
                )
            )
            .scalars()
            .all()
        )

    # Get RSVP counts for each event
    events_with_counts = []
    for event in events:
        invited = event.id in invited_event_ids
        if not _can_view_event(event, actor, invited=invited):
            continue
        rsvp_query = (
            select(EventRSVP.status, func.count(EventRSVP.id).label("count"))
            .where(EventRSVP.event_id == event.id)
            .group_by(EventRSVP.status)
        )

        rsvp_result = await db.execute(rsvp_query)
        rsvp_counts = {row[0]: row[1] for row in rsvp_result.all()}

        events_with_counts.append(
            EventResponse.model_validate(
                _event_response_dict(
                    event,
                    rsvp_counts,
                    actor=actor,
                    invited=invited,
                )
            )
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
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single event by ID."""
    query = select(Event).where(Event.id == event_id)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    actor = await _resolve_event_actor(current_user, db)
    invited = bool(
        actor.member_id
        and (
            await db.execute(
                select(EventInvite.id).where(
                    EventInvite.event_id == event_id,
                    EventInvite.member_id == actor.member_id,
                )
            )
        ).scalar_one_or_none()
    )
    if not _can_view_event(event, actor, invited=invited):
        raise HTTPException(status_code=404, detail="Event not found")

    # Get RSVP counts
    rsvp_query = (
        select(EventRSVP.status, func.count(EventRSVP.id).label("count"))
        .where(EventRSVP.event_id == event.id)
        .group_by(EventRSVP.status)
    )

    rsvp_result = await db.execute(rsvp_query)
    rsvp_counts = {row[0]: row[1] for row in rsvp_result.all()}

    return EventResponse.model_validate(
        _event_response_dict(
            event,
            rsvp_counts,
            actor=actor,
            invited=invited,
        )
    )


@router.post("/", response_model=EventResponse, status_code=201)
async def create_event(
    event_data: EventCreate,
    current_member: MemberRef = Depends(get_current_member),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new event (admin only)."""
    payload = event_data.model_dump()
    event_dict_in = {
        key: value for key, value in payload.items() if key not in PRICING_KEYS
    }
    event = Event(
        **event_dict_in,
        **normalize_event_pricing(payload),
        created_by=current_member.id,
    )

    db.add(event)
    await db.commit()
    await db.refresh(event)

    # Best-effort: provision the event chat channel with the creator as admin.
    await ensure_event_channel(
        event_id=event.id,
        event_title=event.title,
        created_by_member_id=current_member.id,
    )

    return EventResponse.model_validate(
        _event_response_dict(
            event,
            actor=EventActor(current_member.id, frozenset(), True, True),
        )
    )


@router.get(
    "/{event_id}/invites",
    response_model=List[EventInviteResponse],
)
async def list_event_invites(
    event_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List explicit invitees for a private event."""
    event_exists = (
        await db.execute(select(Event.id).where(Event.id == event_id))
    ).scalar_one_or_none()
    if event_exists is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return (
        (
            await db.execute(
                select(EventInvite)
                .where(EventInvite.event_id == event_id)
                .order_by(EventInvite.created_at)
            )
        )
        .scalars()
        .all()
    )


@router.post(
    "/{event_id}/invites",
    response_model=List[EventInviteResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_event_invites(
    event_id: uuid.UUID,
    payload: EventInviteCreate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Add invitees idempotently to an invite-only event."""
    event = (
        await db.execute(select(Event).where(Event.id == event_id))
    ).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    existing_ids = set(
        (
            await db.execute(
                select(EventInvite.member_id).where(
                    EventInvite.event_id == event_id,
                    EventInvite.member_id.in_(payload.member_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    for member_id in payload.member_ids:
        if member_id not in existing_ids:
            db.add(EventInvite(event_id=event_id, member_id=member_id))
    await db.commit()
    return (
        (
            await db.execute(
                select(EventInvite)
                .where(EventInvite.event_id == event_id)
                .order_by(EventInvite.created_at)
            )
        )
        .scalars()
        .all()
    )


@router.delete(
    "/{event_id}/invites/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_event_invite(
    event_id: uuid.UUID,
    member_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove one explicit event invitation."""
    await db.execute(
        delete(EventInvite).where(
            EventInvite.event_id == event_id,
            EventInvite.member_id == member_id,
        )
    )
    await db.commit()
    return None


# ---------------------------------------------------------------------------
# Member-created open-swim meets
# ---------------------------------------------------------------------------

OPEN_SWIM_TYPE = "open_swim"
ADULT_AGE = 18
MAX_UPCOMING_OPEN_SWIMS = 3  # anti-spam: max upcoming meets a member may host


def _age_from_iso(dob_iso: Optional[str]) -> Optional[int]:
    """Whole-year age from an ISO date/datetime string, or None if unparseable."""
    if not dob_iso:
        return None
    try:
        dob = datetime.fromisoformat(dob_iso).date()
    except ValueError:
        try:
            dob = date.fromisoformat(dob_iso[:10])
        except ValueError:
            return None
    today = utc_now().date()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


async def _require_adult(member_id: uuid.UUID) -> None:
    """Raise 403 unless the member is a verified adult (18+)."""
    data = await get_member_by_id(str(member_id), calling_service="events")
    age = _age_from_iso(data.get("date_of_birth") if data else None)
    if age is None or age < ADULT_AGE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Open-swim meets are for adults (18+). Add your date of birth to "
                "your profile to create or join one."
            ),
        )


async def _snapshot_pool_fee(pool_id: uuid.UUID) -> tuple[int, dict]:
    """Validate a member-selectable pool and snapshot its per-swimmer fee (kobo).

    Members may only select active-partner pools that bill *per swimmer* — flat
    -fee pools are rejected so a low-turnout meet can never commit SwimBuddz to a
    fixed cost. Returns ``(pool_fee_kobo, pool_dict)``.
    """
    pool = await get_partner_pool(str(pool_id), calling_service="events")
    if not pool:
        raise HTTPException(
            status_code=400, detail="That pool isn't available for member meets."
        )
    per_swimmer = pool.get("price_per_swimmer_ngn")
    flat = pool.get("flat_session_fee_ngn")
    if flat and float(flat) > 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "That pool charges a flat session fee and can't be used for "
                "member-created meets. Pick a pool that bills per swimmer."
            ),
        )
    if not per_swimmer or float(per_swimmer) <= 0:
        raise HTTPException(
            status_code=400,
            detail="That pool has no per-swimmer rate set, so it can't be used yet.",
        )
    return naira_to_kobo(float(per_swimmer)), pool


@router.post("/open-swim", response_model=EventResponse, status_code=201)
async def create_open_swim(
    payload: OpenSwimCreate,
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a member-hosted open-swim meet.

    Adults-only (18+). If ``pool_id`` is set it must be an active-partner
    per-swimmer pool; the per-swimmer fee is snapshotted and the optional
    organizer surcharge is added. No pool = a free/informal meet.
    """
    await _require_adult(current_member.id)

    # Anti-spam: cap upcoming member-hosted meets.
    upcoming = (
        await db.execute(
            select(func.count(Event.id)).where(
                Event.created_by == current_member.id,
                Event.event_type == OPEN_SWIM_TYPE,
                Event.start_time >= utc_now(),
            )
        )
    ).scalar() or 0
    if upcoming >= MAX_UPCOMING_OPEN_SWIMS:
        raise HTTPException(
            status_code=429,
            detail=(
                f"You already have {MAX_UPCOMING_OPEN_SWIMS} upcoming meets. "
                "Wrap one up before creating another."
            ),
        )

    pool_fee_kobo: Optional[int] = None
    max_capacity = payload.max_capacity
    if payload.pool_id is not None:
        pool_fee_kobo, pool = await _snapshot_pool_fee(payload.pool_id)
        pool_max = pool.get("max_swimmers_capacity")
        if pool_max and (max_capacity is None or max_capacity > pool_max):
            max_capacity = pool_max

    surcharge_kobo = (
        naira_to_kobo(payload.organizer_surcharge_naira)
        if payload.organizer_surcharge_naira
        else 0
    )

    event = Event(
        title=payload.title,
        description=payload.description,
        event_type=OPEN_SWIM_TYPE,
        location=payload.location,
        start_time=payload.start_time,
        end_time=payload.end_time,
        max_capacity=max_capacity,
        tier_access=payload.tier_access,
        pool_id=payload.pool_id,
        pool_fee_kobo=pool_fee_kobo,
        organizer_surcharge_kobo=surcharge_kobo,
        created_by=current_member.id,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    await ensure_event_channel(
        event_id=event.id,
        event_title=event.title,
        created_by_member_id=current_member.id,
    )
    return EventResponse.model_validate(_event_response_dict(event))


async def _rsvp_counts(event_id: uuid.UUID, db: AsyncSession) -> dict:
    """{status: count} for an event's RSVPs."""
    rows = (
        await db.execute(
            select(EventRSVP.status, func.count(EventRSVP.id))
            .where(EventRSVP.event_id == event_id)
            .group_by(EventRSVP.status)
        )
    ).all()
    return {row[0]: row[1] for row in rows}


async def _load_own_open_swim(
    event_id: uuid.UUID, member_id: uuid.UUID, db: AsyncSession
) -> Event:
    """Load an open-swim meet, asserting the caller created it."""
    event = (
        await db.execute(select(Event).where(Event.id == event_id))
    ).scalar_one_or_none()
    if not event or event.event_type != OPEN_SWIM_TYPE:
        raise HTTPException(status_code=404, detail="Meet not found")
    if event.created_by != member_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only manage meets you created.",
        )
    return event


@router.patch("/open-swim/{event_id}", response_model=EventResponse)
async def update_open_swim(
    event_id: uuid.UUID,
    payload: OpenSwimUpdate,
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """Edit a meet you created (creator only)."""
    event = await _load_own_open_swim(event_id, current_member.id, db)

    fields = payload.model_dump(exclude_unset=True)
    if "organizer_surcharge_naira" in fields:
        surcharge = fields.pop("organizer_surcharge_naira")
        event.organizer_surcharge_kobo = naira_to_kobo(surcharge) if surcharge else 0
    for field, value in fields.items():
        setattr(event, field, value)

    await db.commit()
    await db.refresh(event)
    rsvp_counts = await _rsvp_counts(event_id, db)
    return EventResponse.model_validate(_event_response_dict(event, rsvp_counts))


@router.delete("/open-swim/{event_id}", status_code=204, response_model=None)
async def cancel_open_swim(
    event_id: uuid.UUID,
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """Cancel a meet you created (creator only); refund anyone who paid."""
    event = await _load_own_open_swim(event_id, current_member.id, db)

    # Refund paid "going" attendees before deleting. Idempotency keys make the
    # whole cancel safe to retry if any single credit call fails mid-loop.
    total_charge_kobo = _total_charge_kobo(event)
    if total_charge_kobo > 0:
        paid_rsvps = (
            (
                await db.execute(
                    select(EventRSVP).where(
                        EventRSVP.event_id == event_id,
                        EventRSVP.status == "going",
                        EventRSVP.wallet_transaction_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if paid_rsvps:
            refund_bubbles = kobo_to_bubbles(total_charge_kobo)
            members = await get_members_bulk(
                [str(r.member_id) for r in paid_rsvps], calling_service="events"
            )
            auth_by_member = {m["id"]: m.get("auth_id") for m in members}
            for r in paid_rsvps:
                auth_id = auth_by_member.get(str(r.member_id))
                if not auth_id:
                    continue
                await credit_member_wallet(
                    auth_id,
                    amount=refund_bubbles,
                    idempotency_key=f"event-cancel-refund-{event_id}-{r.member_id}",
                    description=f"Refund — '{event.title}' cancelled ({refund_bubbles} 🫧)",
                    calling_service="events",
                    transaction_type="refund",
                    reference_type="event",
                    reference_id=str(event_id),
                )

    await db.execute(delete(EventRSVP).where(EventRSVP.event_id == event_id))
    await db.delete(event)
    await db.commit()
    return None


@router.patch("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: uuid.UUID,
    event_data: EventUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update an event (admin only)."""
    query = select(Event).where(Event.id == event_id)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Update only provided fields. Pricing values need Naira→kobo normalization.
    update_fields = event_data.model_dump(exclude_unset=True)
    pricing_updates = {
        key: update_fields.pop(key)
        for key in list(update_fields)
        if key in PRICING_KEYS
    }
    if pricing_updates:
        pricing_payload = event_pricing_payload(event)
        pricing_payload.update(pricing_updates)
        update_fields.update(normalize_event_pricing(pricing_payload))
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
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete an event (admin only)."""
    query = select(Event).where(Event.id == event_id)
    result = await db.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Delete associated RSVPs first
    await db.execute(delete(EventRSVP).where(EventRSVP.event_id == event_id))
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

    When pay_with_bubbles=True and the member commits to 'going' (and hasn't
    already paid), their wallet is debited for the event fee — this covers both
    a new 'going' RSVP and a maybe/not_going → going switch.
    """
    member_id = current_member.id

    # Check if event exists
    event_query = select(Event).where(Event.id == event_id)
    event_result = await db.execute(event_query)
    event = event_result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    membership = (
        await get_member_membership(
            str(member_id),
            calling_service="events",
        )
        if event.tier_access not in {"public", "community"}
        else {}
    )
    actor = EventActor(
        member_id=member_id,
        paid_tiers=frozenset(active_paid_tiers(membership or {})),
        is_authenticated=True,
        is_admin=False,
    )
    invited = bool(
        (
            await db.execute(
                select(EventInvite.id).where(
                    EventInvite.event_id == event_id,
                    EventInvite.member_id == member_id,
                )
            )
        ).scalar_one_or_none()
    )
    if not _can_attend_event(event, actor, invited=invited):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This event is not available to your membership or invitations.",
        )

    # Check if RSVP already exists
    rsvp_query = select(EventRSVP).where(
        and_(EventRSVP.event_id == event_id, EventRSVP.member_id == member_id)
    )
    rsvp_result = await db.execute(rsvp_query)
    existing_rsvp = rsvp_result.scalar_one_or_none()

    is_open_swim = event.event_type == OPEN_SWIM_TYPE
    total_charge_kobo = _total_charge_kobo(event)
    # Charge when the member commits to "going" and hasn't already paid — this
    # covers both a brand-new "going" RSVP and a maybe/not_going → going switch.
    # The wallet idempotency key is a second guard against any double-debit.
    already_paid = (
        existing_rsvp is not None and existing_rsvp.wallet_transaction_id is not None
    )
    should_charge = (
        rsvp_data.status == "going"
        and rsvp_data.pay_with_bubbles
        and total_charge_kobo > 0
        and not already_paid
    )

    # Adults-only + liability-waiver gates for peer-organized open-swim meets.
    if is_open_swim and rsvp_data.status == "going":
        await _require_adult(member_id)
        if total_charge_kobo > 0 and not rsvp_data.waiver_accepted:
            raise HTTPException(
                status_code=400,
                detail="Please accept the liability waiver to join this meet.",
            )

    # Debit wallet when the member commits to a paid "going" and hasn't paid yet.
    wallet_txn_id = None
    if should_charge:
        try:
            fee_bubbles = kobo_to_bubbles_exact(total_charge_kobo)
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This event cannot be paid entirely with whole Bubbles. "
                    "Use card payment instead."
                ),
            ) from exc
        idempotency_key = f"event-{event_id}-{member_id}"
        try:
            result_txn = await debit_member_wallet(
                current_member.auth_id,
                amount=fee_bubbles,
                idempotency_key=idempotency_key,
                description=f"Event — {event.title} ({fee_bubbles} 🫧)",
                calling_service="events",
                transaction_type="purchase",
                reference_type="event",
                reference_id=str(event_id),
            )
            wallet_txn_id = result_txn.get("transaction_id")
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
        existing_rsvp.updated_at = utc_now()
        if wallet_txn_id is not None:
            existing_rsvp.wallet_transaction_id = wallet_txn_id
        await db.commit()
        await db.refresh(existing_rsvp)
        # Sync chat membership to match new RSVP status.
        await ensure_event_channel(event_id=event_id, event_title=event.title)
        await reconcile_event_membership(
            event_id=event_id,
            member_id=member_id,
            rsvp_id=existing_rsvp.id,
            rsvp_status=existing_rsvp.status,
        )
        return RSVPResponse.model_validate(existing_rsvp)
    else:
        # Create new RSVP
        rsvp = EventRSVP(
            event_id=event_id,
            member_id=member_id,
            status=rsvp_data.status,
            wallet_transaction_id=wallet_txn_id,
        )
        db.add(rsvp)
        await db.commit()
        await db.refresh(rsvp)
        await ensure_event_channel(event_id=event_id, event_title=event.title)
        await reconcile_event_membership(
            event_id=event_id,
            member_id=member_id,
            rsvp_id=rsvp.id,
            rsvp_status=rsvp.status,
        )
        return RSVPResponse.model_validate(rsvp)


@router.get("/{event_id}/rsvps", response_model=List[RSVPResponse])
async def list_event_rsvps(
    event_id: uuid.UUID,
    status: Optional[str] = Query(None, description="Filter by RSVP status"),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all RSVPs for an event (admin only)."""
    query = select(EventRSVP).where(EventRSVP.event_id == event_id)

    if status:
        query = query.where(EventRSVP.status == status)

    result = await db.execute(query)
    rsvps = result.scalars().all()

    return [RSVPResponse.model_validate(rsvp) for rsvp in rsvps]
