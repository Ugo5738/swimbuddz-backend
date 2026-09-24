"""One durable, retryable branded confirmation per member booking / guest pass."""

from __future__ import annotations

import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.currency import kobo_to_bubbles_exact
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from services.sessions_service.models import (
    BookingEmailDelivery,
    GuestPass,
    Session,
    SessionBooking,
)
from services.sessions_service.services.guest_booking import (
    member_guest_url,
    receipt_url,
)

logger = get_logger(__name__)


def allocate_total(total: int, weights: list[int]) -> list[int]:
    """Allocate integer currency/Bubbles without losing units or negative lines."""
    if not weights:
        return []
    if not sum(weights):
        weights = [1] * len(weights)
    denominator = sum(weights)
    parts = [divmod(total * weight, denominator) for weight in weights]
    amounts = [part[0] for part in parts]
    remainder = total - sum(amounts)
    for index in sorted(range(len(parts)), key=lambda i: (-parts[i][1], i))[:remainder]:
        amounts[index] += 1
    return amounts


async def queue_confirmation(
    db: AsyncSession,
    booking_id: uuid.UUID,
    *,
    guest: bool = False,
    payment_details: dict | None = None,
) -> str:
    """Enqueue in the same transaction that confirms payment; never commit here."""
    kind = "guest" if guest else "member"
    key = f"{kind}-booking-confirmation:{booking_id}"
    await db.execute(
        insert(BookingEmailDelivery)
        .values(
            key=key, booking_id=booking_id, kind=kind, payment_details=payment_details
        )
        .on_conflict_do_nothing(index_elements=[BookingEmailDelivery.key])
    )
    return key


def session_email_details(session: Session) -> dict:
    try:
        tz = ZoneInfo(session.timezone or "Africa/Lagos")
    except (ValueError, KeyError):
        tz = ZoneInfo("Africa/Lagos")
    starts = session.starts_at.astimezone(tz)
    ends = session.ends_at.astimezone(tz)
    return {
        "session_title": session.title,
        "session_date": starts.strftime("%A, %d %B %Y"),
        "session_time": f"{starts:%H:%M} – {ends:%H:%M} ({tz.key})",
        "session_location": session.location_name or "Location to be confirmed",
        "session_address": session.location_address or "",
    }


async def deliver_confirmation(db: AsyncSession, key: str) -> bool:
    """A row lock serializes concurrent callbacks; failed sends remain retryable."""
    delivery = (
        await db.execute(
            select(BookingEmailDelivery)
            .where(BookingEmailDelivery.key == key)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if not delivery:
        await db.commit()
        return False
    if delivery.sent_at:
        await db.commit()
        return True
    try:
        model = GuestPass if delivery.kind == "guest" else SessionBooking
        booking = await db.get(model, delivery.booking_id)
        if not booking or booking.status not in {"confirmed", "attended"}:
            # Cancelled bookings should never get a stale confirmation.
            delivery.available_at = utc_now() + timedelta(days=1)
            await db.commit()
            return False
        session = await db.get(Session, booking.session_id)
        if not session or session.status in {"cancelled", "draft"}:
            delivery.available_at = utc_now() + timedelta(days=1)
            await db.commit()
            return False
        data = session_email_details(session)
        if delivery.kind == "guest":
            to_email = booking.email
            template = "guest_pass_confirmation"
            data.update(
                {
                    "guest_name": booking.full_name,
                    "amount_paid": booking.total_kobo / 100,
                    "payment_reference": booking.payment_reference,
                    "receipt_url": receipt_url(booking.id),
                    "post_session": booking.booking_mode == "settlement"
                    or session.starts_at <= utc_now(),
                }
            )
        else:
            member = await get_member_by_auth_id(
                booking.member_auth_id, calling_service="sessions"
            )
            if not member or not member.get("email"):
                raise ValueError("Booking member has no email")
            to_email = member["email"]
            template = "session_confirmation"
            guest_url = None
            try:
                guest_url = await member_guest_url(session, booking.member_auth_id, db)
            except Exception:
                if not db.is_active:
                    raise
                # Sharing is optional enrichment, never a prerequisite for a
                # paid/free booking's transactional confirmation.
                logger.warning(
                    "Guest invitation omitted from confirmation %s", key, exc_info=True
                )
            data.update(
                {
                    "member_name": " ".join(
                        part
                        for part in [member.get("first_name"), member.get("last_name")]
                        if part
                    )
                    or "Member",
                    "member_id": str(booking.member_id),
                    "amount_paid": booking.fee_amount_kobo / 100,
                    "currency": "NGN",
                    "booking_reference": str(booking.id),
                    "guest_booking_url": guest_url,
                    "post_session": session.starts_at <= utc_now(),
                }
            )
            if (
                booking.wallet_transaction_id
                and not booking.payment_intent_id
                and booking.fee_amount_kobo
            ):
                data["amount_paid"] = 0
                data["bubbles_applied"] = kobo_to_bubbles_exact(booking.fee_amount_kobo)
                data["bubbles_amount_ngn"] = booking.fee_amount_kobo / 100
        if delivery.kind == "member" and delivery.payment_details:
            data.update(delivery.payment_details)
        delivery.attempts += 1
        sent = await get_email_client().send_template(
            template_type=template, to_email=to_email, template_data=data
        )
        if sent:
            delivery.sent_at = utc_now()
            booking.confirmation_email_sent_at = delivery.sent_at
        delivery.available_at = utc_now() + timedelta(
            minutes=min(60, 2 ** min(delivery.attempts, 6))
        )
        await db.commit()
        return bool(sent)
    except Exception:
        logger.warning("Booking confirmation delivery failed: %s", key, exc_info=True)
        if db.is_active:
            delivery.attempts += 1
            delivery.available_at = utc_now() + timedelta(
                minutes=min(60, 2 ** min(delivery.attempts, 6))
            )
            await db.commit()
        else:
            await db.rollback()
        return False


async def retry_confirmations() -> None:
    from libs.db.config import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        keys = list(
            (
                await db.execute(
                    select(BookingEmailDelivery.key)
                    .where(
                        BookingEmailDelivery.sent_at.is_(None),
                        BookingEmailDelivery.available_at <= utc_now(),
                    )
                    .order_by(BookingEmailDelivery.available_at)
                    .limit(50)
                )
            ).scalars()
        )
        await db.commit()
        for key in keys:
            await deliver_confirmation(db, key)
