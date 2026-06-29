"""Events Service router/endpoints."""

import uuid
from datetime import date, datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.currency import kobo_to_bubbles, naira_to_kobo
from libs.common.service_client import (
    credit_member_wallet,
    debit_member_wallet,
    get_member_by_id,
    get_members_bulk,
    get_partner_pool,
)
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.events_service.services.chat_sync import (
    ensure_event_channel,
    reconcile_event_membership,
)
from services.events_service.models import Event, EventRSVP, MemberRef
from services.events_service.schemas import (
    EventCreate,
    EventResponse,
    EventUpdate,
    OpenSwimCreate,
    OpenSwimUpdate,
    RSVPCreate,
    RSVPResponse,
)

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


def _event_response_dict(event: Event, rsvp_count: dict | None = None) -> dict:
    """Build an EventResponse-compatible dict, converting kobo → naira."""
    total_kobo = _total_charge_kobo(event)
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
        "pool_id": event.pool_id,
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
        query = query.where(Event.start_time >= utc_now())

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
    current_member: MemberRef = Depends(get_current_member),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new event (admin only)."""
    event_dict_in = event_data.model_dump(exclude={"cost_naira"})
    # Convert naira → kobo for DB storage
    cost_naira = event_data.cost_naira
    event_dict_in["cost_kobo"] = (
        naira_to_kobo(cost_naira) if cost_naira is not None else None
    )
    event = Event(**event_dict_in, created_by=current_member.id)

    db.add(event)
    await db.commit()
    await db.refresh(event)

    # Best-effort: provision the event chat channel with the creator as admin.
    await ensure_event_channel(
        event_id=event.id,
        event_title=event.title,
        created_by_member_id=current_member.id,
    )

    return EventResponse.model_validate(_event_response_dict(event))


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
        fee_bubbles = kobo_to_bubbles(total_charge_kobo)
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
