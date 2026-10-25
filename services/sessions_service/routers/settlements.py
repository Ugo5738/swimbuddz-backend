"""Service-authenticated settlement of existing member and guest walk-ins."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.db.session import get_async_db
from services.sessions_service.models import (
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionParticipant,
)
from services.sessions_service.schemas.settlement import (
    SessionSettlementConfirmRequest,
    SessionSettlementLine,
    SessionSettlementQuoteRequest,
    SessionSettlementQuoteResponse,
)
from services.sessions_service.services.participants import ensure_session_participants

router = APIRouter(
    prefix="/internal/sessions/settlements",
    tags=["internal-settlements"],
    dependencies=[Depends(require_service_role)],
)


async def _load_quote(
    db: AsyncSession,
    payload: SessionSettlementQuoteRequest,
    *,
    confirming: bool = False,
) -> tuple[
    list[SessionBooking],
    list[SessionParticipant],
    list[Session],
    SessionSettlementQuoteResponse,
]:
    # Global order agrees with the normal booking confirmation path.
    refs = list(
        (
            await db.execute(
                select(SessionBooking.session_id).where(
                    SessionBooking.id.in_(payload.booking_ids)
                )
            )
        ).scalars()
    )
    refs += list(
        (
            await db.execute(
                select(SessionParticipant.session_id).where(
                    SessionParticipant.id.in_(payload.guest_participant_ids)
                )
            )
        ).scalars()
    )
    sessions = list(
        (
            await db.execute(
                select(Session)
                .where(Session.id.in_(set(refs)))
                .order_by(Session.id)
                .with_for_update()
            )
        ).scalars()
    )
    session_map = {row.id: row for row in sessions}
    session_types = {row.session_type.value for row in sessions}
    if len(session_types) > 1:
        raise HTTPException(422, "Combine charges belonging to the same session type")
    bookings = list(
        (
            await db.execute(
                select(SessionBooking)
                .where(SessionBooking.id.in_(payload.booking_ids))
                .order_by(SessionBooking.id)
                .with_for_update()
            )
        ).scalars()
    )
    guests = list(
        (
            await db.execute(
                select(SessionParticipant)
                .where(SessionParticipant.id.in_(payload.guest_participant_ids))
                .order_by(SessionParticipant.id)
                .with_for_update()
            )
        ).scalars()
    )
    if len(bookings) != len(payload.booking_ids) or len(guests) != len(
        payload.guest_participant_ids
    ):
        raise HTTPException(404, "A settlement booking or guest was not found")

    lines = []
    for booking in bookings:
        if booking.member_auth_id != payload.member_auth_id:
            raise HTTPException(409, "A booking belongs to a different member")
        if booking.status != SessionBookingStatus.CONFIRMED or booking.party_size != 1:
            raise HTTPException(
                409, "Settlement requires confirmed individual walk-in bookings"
            )
        if booking.wallet_transaction_id or (
            booking.payment_intent_id is not None
            and (
                not confirming or booking.payment_intent_id != payload.payment_intent_id
            )
        ):
            raise HTTPException(409, "A booking is already settled")
        session = session_map.get(booking.session_id)
        if session is None:
            raise HTTPException(404, "Settlement session not found")
        if booking.fee_amount_kobo <= 0:
            raise HTTPException(409, "A booking has no outstanding fee")
        lines.append(
            SessionSettlementLine(
                kind="booking",
                id=booking.id,
                session_id=session.id,
                session_title=session.title,
                session_starts_at=session.starts_at,
                name="Member",
                amount_kobo=booking.fee_amount_kobo,
            )
        )
    for guest in guests:
        if (
            guest.source != "walk_in"
            or guest.participant_kind != "guest"
            or guest.booking_id
            or guest.guest_pass_id
            or guest.booking_guest_id
        ):
            raise HTTPException(
                409, "Guest settlement requires a standalone guest walk-in"
            )
        same_receipt = (
            confirming
            and guest.payment_status == "paid"
            and guest.payment_reference == payload.payment_reference
        )
        if not same_receipt and (
            guest.payment_status != "unpaid" or guest.payment_reference
        ):
            raise HTTPException(
                409, "A guest is already settled or needs reconciliation"
            )
        session = session_map.get(guest.session_id)
        if session is None:
            raise HTTPException(404, "Settlement session not found")
        if guest.fee_amount_kobo <= 0:
            raise HTTPException(409, "A guest has no outstanding fee")
        lines.append(
            SessionSettlementLine(
                kind="guest_walk_in",
                id=guest.id,
                session_id=session.id,
                session_title=session.title,
                session_starts_at=session.starts_at,
                name=guest.full_name_snapshot,
                amount_kobo=guest.fee_amount_kobo,
            )
        )
    lines.sort(key=lambda line: (line.session_starts_at, line.kind, str(line.id)))
    return (
        bookings,
        guests,
        sessions,
        SessionSettlementQuoteResponse(
            member_auth_id=payload.member_auth_id,
            session_type=next(iter(session_types)),
            total_kobo=sum(line.amount_kobo for line in lines),
            lines=lines,
        ),
    )


@router.post("/quote", response_model=SessionSettlementQuoteResponse)
async def quote_settlement(
    payload: SessionSettlementQuoteRequest,
    db: AsyncSession = Depends(get_async_db),
) -> SessionSettlementQuoteResponse:
    *_, quote = await _load_quote(db, payload)
    return quote


@router.post("/confirm")
async def confirm_settlement(
    payload: SessionSettlementConfirmRequest,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    bookings, guests, sessions, quote = await _load_quote(db, payload, confirming=True)

    # Freeze the commercial identity and amount per person/session. Labels may
    # change after issuance without changing the amount owed.
    def identity(line: SessionSettlementLine) -> tuple:
        return line.kind, line.id, line.session_id, line.amount_kobo

    if quote.total_kobo != payload.total_kobo or sorted(
        map(identity, quote.lines)
    ) != sorted(map(identity, payload.lines)):
        raise HTTPException(409, "The recorded charges differ from the paid settlement")
    for booking in bookings:
        booking.payment_intent_id = payload.payment_intent_id
    await db.flush()
    for session in sessions:
        await ensure_session_participants(db, session)
    for guest in guests:
        guest.payment_status = "paid"
        guest.payment_method = "paystack"
        guest.payment_reference = payload.payment_reference
        guest.paid_at = payload.paid_at
    await db.commit()
    return {"settled": len(quote.lines), "total_kobo": quote.total_kobo}
