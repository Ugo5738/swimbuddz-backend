"""Member/+1 and public-guest tickets; price inputs are entirely server-owned."""

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import (
    get_current_user,
    get_optional_user,
    require_service_role,
)
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import internal_post
from libs.db.session import get_async_db
from services.members_service.models import (
    CommunityExperienceOffering,
    CommunityExperiencePurchase,
)
from services.members_service.models.experience import (
    CommunityExperienceOrder,
    CommunityExperienceParticipant,
)
from services.members_service.routers.community_experiences import (
    _assert_purchase_window,
    _member_by_auth,
    _quote_for_member,
)
from services.members_service.routers.experience_admin import lock_offering
from services.members_service.schemas.experience import (
    ExperienceOrderAccess,
    ExperienceOrderConfirm,
    ExperienceOrderCreate,
    ExperienceOrderResponse,
    ExperienceParticipantInput,
)
from services.members_service.services.experience_events import (
    effective_capacity,
    live_events,
    public_event,
)
from services.members_service.services.experience_ticketing import (
    assert_capacity,
    existing_member_guests,
    hold_expiry,
    make_participant,
    token_hash,
    verify_order_token,
)

router = APIRouter(prefix="/clubs/community-experiences", tags=["experience-tickets"])


def order_response(order):
    return ExperienceOrderResponse(
        **{
            column.name: getattr(order, column.name)
            for column in CommunityExperienceOrder.__table__.columns
        },
        participant_count=len(order.participants),
        tickets=[
            {
                "id": str(person.id),
                "full_name": person.full_name,
                "ticket_kind": person.ticket_kind,
                "price_kobo": person.price_kobo,
                "needs_safety_details": not bool(
                    person.waiver_accepted_at and person.emergency_contact.get("phone")
                ),
            }
            for person in order.participants
        ],
    )


@router.get("/public/{offering_id}")
async def public_offering(
    offering_id: uuid.UUID,
    current_user: AuthUser | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    offering = await db.get(CommunityExperienceOffering, offering_id)
    if not offering or not offering.is_active:
        raise HTTPException(404, "Community Experience not found")
    events = await live_events(offering)
    if not events or any(event["status"] != "published" for event in events):
        raise HTTPException(404, "This Experience has not been published yet")
    if not current_user and any(event["visibility"] != "public" for event in events):
        raise HTTPException(401, "Sign in to view this members' Experience")
    return {
        "id": offering.id,
        "name": offering.name,
        "currency": offering.currency,
        "period_start": offering.period_start,
        "period_end": offering.period_end,
        "ticket_options": [
            {"kind": kind, "amount_kobo": amount}
            for kind, amount in (
                ("standard_member", offering.standard_member_fee_kobo),
                ("club_member", offering.club_member_fee_kobo),
                ("club_bundle", offering.club_bundle_fee_kobo),
                ("member_guest", offering.member_guest_fee_kobo),
                ("public_guest", offering.public_guest_fee_kobo),
            )
            if amount is not None
        ],
        "max_guests_per_member": offering.max_guests_per_member,
        "capacity": effective_capacity(offering, events),
        "purchase_opens_at": offering.purchase_opens_at,
        "purchase_closes_at": offering.purchase_closes_at,
        "events": [public_event(event) for event in events],
    }


@router.post(
    "/{offering_id}/orders", response_model=ExperienceOrderResponse, status_code=201
)
async def create_order(
    offering_id: uuid.UUID,
    body: ExperienceOrderCreate,
    current_user: AuthUser | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    member = await _member_by_auth(db, current_user.user_id) if current_user else None
    offering = await lock_offering(db, offering_id)
    existing = (
        await db.execute(
            select(CommunityExperienceOrder).where(
                CommunityExperienceOrder.idempotency_key == str(body.idempotency_key)
            )
        )
    ).scalar_one_or_none()
    if existing:
        verify_order_token(existing, body.access_token)
        if existing.offering_id != offering.id or existing.member_id != (
            member.id if member else None
        ):
            raise HTTPException(409, "This checkout key belongs to another order")
        return order_response(existing)
    _assert_purchase_window(offering)
    events = await live_events(offering, for_sale=True)
    participants = []
    membership_fee, membership_months = 0, 0
    if member:
        if offering.currency != "NGN":
            raise HTTPException(
                422,
                "Member Experience checkout currently requires NGN for annual Membership pricing",
            )
        quote = await _quote_for_member(db, offering=offering, member=member)
        if body.include_member:
            if quote.already_purchased:
                raise HTTPException(
                    409,
                    "You already have an Experience ticket; choose guests only to add your +1s",
                )
            # A second pending member order must not reserve/pay the same ticket twice.
            from services.members_service.services.experience_ticketing import (
                live_order_condition,
            )

            held = (
                await db.execute(
                    select(CommunityExperienceParticipant.id)
                    .join(CommunityExperienceOrder)
                    .where(
                        CommunityExperienceOrder.offering_id == offering.id,
                        CommunityExperienceParticipant.member_id == member.id,
                        live_order_condition(),
                    )
                )
            ).first()
            if held:
                raise HTTPException(
                    409, "You already have an active checkout for this Experience"
                )
            participants.append(
                make_participant(
                    body.participant,
                    quote.price_context,
                    quote.amount_kobo,
                    member_id=member.id,
                )
            )
            membership_fee, membership_months = (
                quote.annual_membership_fee_kobo,
                quote.annual_membership_months,
            )
        elif not quote.already_purchased:
            raise HTTPException(
                409,
                "Guests-only checkout requires your own confirmed Experience ticket",
            )
        if body.guests and (
            offering.member_guest_fee_kobo is None
            or len(body.guests)
            + await existing_member_guests(db, offering.id, member.id)
            > offering.max_guests_per_member
        ):
            raise HTTPException(
                422,
                "Guest tickets are disabled or exceed this Experience's per-member limit",
            )
        participants.extend(
            make_participant(guest, "member_guest", offering.member_guest_fee_kobo)
            for guest in body.guests
        )
    else:
        if not body.include_member:
            raise HTTPException(
                422, "Public checkout requires the named primary participant"
            )
        if offering.public_guest_fee_kobo is None or body.guests:
            raise HTTPException(
                422,
                "Public guest tickets are unavailable, or require one named participant per checkout",
            )
        if any(event.get("visibility") != "public" for event in events):
            raise HTTPException(409, "This Experience is not open to public guests")
        participants.append(
            make_participant(
                body.participant, "public_guest", offering.public_guest_fee_kobo
            )
        )
    if not participants:
        raise HTTPException(422, "Add at least one participant")
    await assert_capacity(
        db, offering.id, effective_capacity(offering, events), len(participants)
    )
    order = CommunityExperienceOrder(
        offering_id=offering.id,
        member_id=member.id if member else None,
        member_auth_id=member.auth_id if member else None,
        payer_email=str(body.participant.email).lower(),
        access_token_hash=token_hash(body.access_token),
        idempotency_key=str(body.idempotency_key),
        status="pending_payment",
        expires_at=hold_expiry(),
        payment_reference=f"EXPERIENCE-{uuid.uuid4().hex.upper()}",
        currency=offering.currency,
        amount_kobo=sum(participant.price_kobo for participant in participants)
        + membership_fee,
        membership_fee_kobo=membership_fee,
        membership_months=membership_months,
        participants=participants,
    )
    db.add(order)
    await db.commit()
    return order_response(order)


async def _order_access(db, order_id, token):
    order = await db.get(CommunityExperienceOrder, order_id)
    if not order:
        raise HTTPException(404, "Experience order not found")
    verify_order_token(order, token)
    return order


@router.post("/orders/{order_id}/status", response_model=ExperienceOrderResponse)
async def order_status(
    order_id: uuid.UUID,
    body: ExperienceOrderAccess,
    db: AsyncSession = Depends(get_async_db),
):
    order = await _order_access(db, order_id, body.access_token)
    response = order_response(order)
    if order.status == "confirmed":
        offering = await db.get(CommunityExperienceOffering, order.offering_id)
        response.events = [
            public_event(event, reveal_private=True)
            for event in await live_events(offering)
        ]
    return response


@router.get("/{offering_id}/tickets/me", response_model=list[ExperienceOrderResponse])
async def my_tickets(
    offering_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    orders = (
        (
            await db.execute(
                select(CommunityExperienceOrder).where(
                    CommunityExperienceOrder.offering_id == offering_id,
                    CommunityExperienceOrder.member_auth_id == current_user.user_id,
                    CommunityExperienceOrder.status == "confirmed",
                )
            )
        )
        .scalars()
        .all()
    )
    offering = await db.get(CommunityExperienceOffering, offering_id)
    events = (
        [
            public_event(event, reveal_private=True)
            for event in await live_events(offering)
        ]
        if orders
        else []
    )
    result = []
    for order in orders:
        response = order_response(order)
        response.events = events
        result.append(response)
    return result


@router.put("/participants/{participant_id}/my-details")
async def complete_member_details(
    participant_id: uuid.UUID,
    body: ExperienceParticipantInput,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    participant = await db.get(CommunityExperienceParticipant, participant_id)
    order = (
        await db.get(CommunityExperienceOrder, participant.order_id)
        if participant
        else None
    )
    if (
        not order
        or order.member_auth_id != current_user.user_id
        or order.status != "confirmed"
    ):
        raise HTTPException(404, "Confirmed participant not found")
    if participant.waiver_accepted_at:
        raise HTTPException(
            409, "Safety details already recorded; contact Admin for a correction"
        )
    details = make_participant(body, participant.ticket_kind, participant.price_kobo)
    for key in (
        "full_name",
        "email",
        "phone",
        "emergency_contact",
        "waiver_accepted_at",
    ):
        setattr(participant, key, getattr(details, key))
    await db.commit()
    return {"completed": True}


@router.post("/orders/{order_id}/checkout")
async def order_checkout(
    order_id: uuid.UUID,
    body: ExperienceOrderAccess,
    db: AsyncSession = Depends(get_async_db),
):
    order = await _order_access(db, order_id, body.access_token)
    if order.status == "confirmed":
        return {"confirmed": True, "reference": order.payment_reference}
    if order.status != "pending_payment" or order.expires_at <= utc_now():
        raise HTTPException(
            409,
            "This seat reservation expired; create a new order and review its price",
        )
    offering = await db.get(CommunityExperienceOffering, order.offering_id)
    _assert_purchase_window(offering)
    await live_events(offering, for_sale=True)
    if order.amount_kobo == 0:
        await confirm_order(
            order.id,
            ExperienceOrderConfirm(
                payment_reference=order.payment_reference, amount_kobo=0
            ),
            db=db,
        )
        return {"confirmed": True, "reference": order.payment_reference}
    try:
        response = await internal_post(
            service_url=get_settings().PAYMENTS_SERVICE_URL,
            path="/internal/payments/initialize",
            calling_service="members",
            json={
                "purpose": "community_experience",
                "amount": order.amount_kobo / 100,
                "currency": order.currency,
                "reference": order.payment_reference,
                "member_auth_id": order.member_auth_id
                or f"experience-guest:{order.id}",
                "callback_url": f"/experiences/{offering.id}?order_id={order.id}",
                "metadata": {
                    "experience_order_id": str(order.id),
                    "community_experience_offering_id": str(offering.id),
                    "payer_email": order.payer_email,
                    "experience_order_amount_kobo": order.amount_kobo,
                },
            },
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            503,
            "Payment initialization was uncertain; retry this same order, not a new payment",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(503, "Could not start payment; retry this order")
    checkout = response.json()
    order.checkout_url = checkout.get("authorization_url")
    await db.commit()
    return checkout


@router.post(
    "/internal/orders/{order_id}/confirm", dependencies=[Depends(require_service_role)]
)
async def confirm_order(
    order_id: uuid.UUID,
    body: ExperienceOrderConfirm,
    db: AsyncSession = Depends(get_async_db),
):
    initial = await db.get(CommunityExperienceOrder, order_id)
    if initial is None:
        raise HTTPException(404, "Experience order not found")
    offering = await lock_offering(db, initial.offering_id)
    order = (
        await db.execute(
            select(CommunityExperienceOrder)
            .where(CommunityExperienceOrder.id == order_id)
            .with_for_update()
        )
    ).scalar_one()
    if (
        body.payment_reference != order.payment_reference
        or body.amount_kobo != order.amount_kobo
    ):
        raise HTTPException(409, "Payment does not match this frozen Experience order")
    if order.status == "confirmed":
        return {"confirmed": True}
    events = await live_events(offering, for_sale=True)
    await assert_capacity(
        db,
        offering.id,
        effective_capacity(offering, events),
        len(order.participants),
        exclude_order_id=order.id,
    )
    if order.membership_months and order.member_auth_id:
        response = await internal_post(
            service_url=get_settings().MEMBERS_SERVICE_URL,
            path=f"/admin/members/by-auth/{order.member_auth_id}/community/extend",
            calling_service="members",
            json={
                "months": order.membership_months,
                "idempotency_key": f"experience-order:{order.id}:membership",
                "source_reference": order.payment_reference,
            },
        )
        if response.status_code >= 400:
            raise HTTPException(
                503,
                "Could not apply the order's annual Membership; fulfillment will retry",
            )
    for participant in order.participants:
        if participant.member_id:
            existing = (
                await db.execute(
                    select(CommunityExperiencePurchase).where(
                        CommunityExperiencePurchase.member_id == participant.member_id,
                        CommunityExperiencePurchase.offering_id == offering.id,
                    )
                )
            ).scalar_one_or_none()
            if existing and existing.payment_reference != order.payment_reference:
                raise HTTPException(
                    409,
                    "This member already has a ticket from another payment; reconcile the duplicate",
                )
            await db.execute(
                insert(CommunityExperiencePurchase)
                .values(
                    member_id=participant.member_id,
                    offering_id=offering.id,
                    price_context=participant.ticket_kind,
                    amount_paid_kobo=participant.price_kobo,
                    payment_reference=order.payment_reference,
                )
                .on_conflict_do_nothing(index_elements=["member_id", "offering_id"])
            )
    order.status = "confirmed"
    await db.commit()
    return {"confirmed": True}


@router.get(
    "/internal/{offering_id}/events/{event_id}/participants",
    dependencies=[Depends(require_service_role)],
)
async def event_participants(
    offering_id: uuid.UUID,
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    from services.members_service.models.experience import CommunityExperienceEvent

    link = (
        await db.execute(
            select(CommunityExperienceEvent.id).where(
                CommunityExperienceEvent.offering_id == offering_id,
                CommunityExperienceEvent.event_id == event_id,
            )
        )
    ).first()
    if not link:
        raise HTTPException(404, "Event is not included in this Experience")
    participants = (
        (
            await db.execute(
                select(CommunityExperienceParticipant)
                .join(CommunityExperienceOrder)
                .where(
                    CommunityExperienceOrder.offering_id == offering_id,
                    CommunityExperienceOrder.status == "confirmed",
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        {"id": person.id, "member_id": person.member_id, "email": person.email}
        for person in participants
    ]
