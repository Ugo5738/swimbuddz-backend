"""One capacity and price authority for named Experience participants."""

import hashlib
import hmac
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from libs.common.datetime_utils import utc_now
from services.members_service.models import CommunityExperiencePurchase
from services.members_service.models.experience import (
    CommunityExperienceOrder,
    CommunityExperienceParticipant,
)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_order_token(order, token: str):
    if not hmac.compare_digest(order.access_token_hash, token_hash(token)):
        raise HTTPException(403, "Invalid order access token")


def live_order_condition():
    return or_(
        CommunityExperienceOrder.status == "confirmed",
        and_(
            CommunityExperienceOrder.status == "pending_payment",
            CommunityExperienceOrder.expires_at > utc_now(),
        ),
    )


async def occupied_places(db, offering_id, *, exclude_order_id=None):
    conditions = [
        CommunityExperienceOrder.offering_id == offering_id,
        live_order_condition(),
    ]
    if exclude_order_id:
        conditions.append(CommunityExperienceOrder.id != exclude_order_id)
    tickets = (
        await db.execute(
            select(func.count(CommunityExperienceParticipant.id))
            .join(CommunityExperienceOrder)
            .where(*conditions)
        )
    ).scalar_one() or 0
    represented_members = (
        select(CommunityExperienceParticipant.member_id)
        .join(CommunityExperienceOrder)
        .where(
            CommunityExperienceOrder.offering_id == offering_id,
            CommunityExperienceOrder.status == "confirmed",
            CommunityExperienceParticipant.member_id.is_not(None),
        )
    )
    legacy = (
        await db.execute(
            select(func.count(CommunityExperiencePurchase.id)).where(
                CommunityExperiencePurchase.offering_id == offering_id,
                CommunityExperiencePurchase.status == "active",
                CommunityExperiencePurchase.member_id.not_in(represented_members),
            )
        )
    ).scalar_one() or 0
    return tickets + legacy


async def assert_capacity(
    db, offering_id, capacity, party_size, *, exclude_order_id=None
):
    if (
        capacity is not None
        and await occupied_places(db, offering_id, exclude_order_id=exclude_order_id)
        + party_size
        > capacity
    ):
        raise HTTPException(
            409, "This Community Experience does not have enough places"
        )


async def existing_member_guests(db, offering_id, member_id):
    return (
        await db.execute(
            select(func.count(CommunityExperienceParticipant.id))
            .join(CommunityExperienceOrder)
            .where(
                CommunityExperienceOrder.offering_id == offering_id,
                CommunityExperienceOrder.member_id == member_id,
                CommunityExperienceParticipant.ticket_kind == "member_guest",
                live_order_condition(),
            )
        )
    ).scalar_one() or 0


def make_participant(details, kind, amount, *, member_id=None):
    return CommunityExperienceParticipant(
        member_id=member_id,
        ticket_kind=kind,
        full_name=details.full_name.strip(),
        email=str(details.email).lower(),
        phone=details.phone.strip(),
        price_kobo=amount,
        waiver_accepted_at=utc_now(),
        emergency_contact={
            "name": details.emergency_contact_name.strip(),
            "phone": details.emergency_contact_phone.strip(),
        },
    )


def hold_expiry():
    return utc_now() + timedelta(minutes=30)


async def reserve_bundle_ticket(
    db, *, offering, member, payment_reference, amount_kobo
):
    import secrets
    from services.members_service.services.experience_events import (
        effective_capacity,
        live_events,
    )

    rows = await live_events(offering, for_sale=True)
    existing = (
        await db.execute(
            select(CommunityExperienceOrder).where(
                CommunityExperienceOrder.payment_reference == payment_reference
            )
        )
    ).scalar_one_or_none()
    if existing:
        if (
            existing.offering_id != offering.id
            or existing.member_id != member.id
            or existing.amount_kobo != amount_kobo
        ):
            raise HTTPException(
                409, "Club bundle reservation does not match this payment"
            )
        if existing.status != "confirmed":
            await assert_capacity(
                db,
                offering.id,
                effective_capacity(offering, rows),
                1,
                exclude_order_id=existing.id,
            )
            existing.status, existing.expires_at = "pending_payment", hold_expiry()
        return existing
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
            409, "This member already has an Experience ticket or active checkout"
        )
    await assert_capacity(db, offering.id, effective_capacity(offering, rows), 1)
    order = CommunityExperienceOrder(
        offering_id=offering.id,
        member_id=member.id,
        member_auth_id=member.auth_id,
        payer_email=member.email,
        access_token_hash=token_hash(secrets.token_urlsafe(32)),
        idempotency_key=f"bundle:{token_hash(payment_reference)}",
        payment_reference=payment_reference,
        amount_kobo=amount_kobo,
        currency=offering.currency,
        membership_fee_kobo=0,
        membership_months=0,
        status="pending_payment",
        expires_at=hold_expiry(),
        participants=[
            CommunityExperienceParticipant(
                member_id=member.id,
                full_name=f"{member.first_name} {member.last_name}",
                email=member.email,
                phone="",
                emergency_contact={},
                waiver_accepted_at=None,
                ticket_kind="club_bundle",
                price_kobo=amount_kobo,
            )
        ],
    )
    db.add(order)
    await db.flush()
    return order


async def confirm_bundle_ticket(
    db, *, offering, member, payment_reference, amount_kobo
):
    from services.members_service.services.experience_events import (
        effective_capacity,
        live_events,
    )

    order = await reserve_bundle_ticket(
        db,
        offering=offering,
        member=member,
        payment_reference=payment_reference,
        amount_kobo=amount_kobo,
    )
    if order.status != "confirmed":
        events = await live_events(offering, for_sale=True)
        await assert_capacity(
            db,
            offering.id,
            effective_capacity(offering, events),
            1,
            exclude_order_id=order.id,
        )
        order.status = "confirmed"
    return order
