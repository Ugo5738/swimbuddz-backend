"""SessionBooking endpoints — member-facing + admin filter.

Routes (mounted under /sessions by app/main.py):

  POST /sessions/{session_id}/book               — member self-book (PENDING)
  POST /sessions/bookings/{booking_id}/confirm   — flip PENDING → CONFIRMED after payment
  POST /sessions/bookings/{booking_id}/cancel    — member or admin cancel
  GET  /sessions/{session_id}/bookings           — admin: list CONFIRMED bookings for a session

The booking lifecycle is intent-only. Day-of attendance still goes through
attendance_service's sign-in flow — that's what creates the
``AttendanceRecord`` and links it back here via ``booking_id``.

See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.currency import kobo_to_bubbles
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    credit_member_wallet,
    debit_member_wallet,
    get_member_by_auth_id,
)
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.sessions_service.models import (
    BookingChannel,
    Session,
    SessionBooking,
    SessionBookingStatus,
)
from services.sessions_service.schemas import (
    BookingConfirmRequest,
    SessionBookingCreate,
    SessionBookingResponse,
)

logger = get_logger(__name__)
router = APIRouter(tags=["bookings"])

# PENDING bookings expire 15 minutes after `booked_at` if not CONFIRMED.
# A 5-min worker sweep flips expired rows to status=EXPIRED, freeing the seat.
PENDING_TTL_MINUTES = 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_member_for_user(
    user: AuthUser,
) -> tuple[uuid.UUID, str]:
    """Resolve (member_id, member_auth_id) from the authenticated user.

    Booking endpoints need the canonical members_service member_id rather
    than just the Supabase auth_id. We look it up via the cross-service
    client so the booking row carries the right FK targets.
    """
    member = await get_member_by_auth_id(user.user_id, calling_service="sessions")
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Complete registration first.",
        )
    return uuid.UUID(member["id"]), user.user_id


# ---------------------------------------------------------------------------
# Member: book a session
# ---------------------------------------------------------------------------


@router.post(
    "/sessions/{session_id}/book",
    response_model=SessionBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def book_session(
    session_id: uuid.UUID,
    booking_in: SessionBookingCreate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Pre-book a session as the authenticated member.

    Creates a SessionBooking(status=PENDING) with a 15-minute TTL.
    Frontend / payments_service is expected to call
    POST /sessions/bookings/{id}/confirm after payment clears.
    """
    if booking_in.session_id != session_id:
        raise HTTPException(
            status_code=422,
            detail="booking session_id does not match URL session_id",
        )

    # Confirm the session exists locally.
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    member_id, member_auth_id = await _resolve_member_for_user(current_user)

    # Idempotency: pre-existing PENDING/CONFIRMED for this (session, member)
    # → return it. CANCELLED/EXPIRED → require admin re-issue.
    existing = (
        await db.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == session_id,
                SessionBooking.member_id == member_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status in (
            SessionBookingStatus.PENDING,
            SessionBookingStatus.CONFIRMED,
        ):
            return existing
        raise HTTPException(
            status_code=409,
            detail=(
                f"A previous booking for this session exists with "
                f"status={existing.status.value}. Contact support to re-book."
            ),
        )

    now = utc_now()

    # Fast path: free session OR member elected to pay full Bubbles.
    # Mirrors the existing one-click sign-in UX: create PENDING → debit
    # wallet (if non-zero fee) → flip CONFIRMED in one transaction.
    if booking_in.pay_with_bubbles:
        wallet_txn_id: Optional[uuid.UUID] = None
        if booking_in.fee_amount_kobo > 0:
            try:
                result_txn = await debit_member_wallet(
                    member_auth_id,
                    amount=kobo_to_bubbles(booking_in.fee_amount_kobo),
                    idempotency_key=f"booking-fee-{session_id}-{member_id}",
                    description=f"Session booking — {session.title}",
                    calling_service="sessions",
                    transaction_type="purchase",
                    reference_type="session_booking",
                    reference_id=f"{session_id}",
                )
                txn = result_txn.get("transaction_id")
                if txn:
                    wallet_txn_id = uuid.UUID(txn)
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

        booking = SessionBooking(
            session_id=session_id,
            member_id=member_id,
            member_auth_id=member_auth_id,
            status=SessionBookingStatus.CONFIRMED,
            channel=BookingChannel.MEMBER_SELF,
            fee_amount_kobo=booking_in.fee_amount_kobo,
            notes=booking_in.notes,
            wallet_transaction_id=wallet_txn_id,
            booked_at=now,
            confirmed_at=now,
        )
        db.add(booking)
        await db.commit()
        await db.refresh(booking)
        return booking

    # Default Paystack path: create PENDING; frontend confirms after verify.
    booking = SessionBooking(
        session_id=session_id,
        member_id=member_id,
        member_auth_id=member_auth_id,
        status=SessionBookingStatus.PENDING,
        channel=BookingChannel.MEMBER_SELF,
        fee_amount_kobo=booking_in.fee_amount_kobo,
        notes=booking_in.notes,
        booked_at=now,
        expires_at=now + timedelta(minutes=PENDING_TTL_MINUTES),
    )
    db.add(booking)
    await db.commit()
    await db.refresh(booking)
    return booking


# ---------------------------------------------------------------------------
# Member: confirm payment cleared (called by frontend after Paystack verify or
# Bubbles debit; future: payments_service webhook will call the internal
# variant instead — see /internal/sessions/bookings/{id}/confirm in
# routers/internal.py).
# ---------------------------------------------------------------------------


@router.post(
    "/sessions/bookings/{booking_id}/confirm",
    response_model=SessionBookingResponse,
)
async def confirm_booking(
    booking_id: uuid.UUID,
    confirm_in: BookingConfirmRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Flip a PENDING booking to CONFIRMED.

    Member can only confirm their own bookings. PENDING and not-yet-expired
    only — EXPIRED/CANCELLED/already-CONFIRMED are rejected.
    """
    booking = (
        await db.execute(select(SessionBooking).where(SessionBooking.id == booking_id))
    ).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.member_auth_id != current_user.user_id:
        raise HTTPException(
            status_code=403, detail="You can only confirm your own bookings."
        )
    if booking.status == SessionBookingStatus.CONFIRMED:
        return booking
    if booking.status != SessionBookingStatus.PENDING:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot confirm a booking with status={booking.status.value}.",
        )
    if booking.expires_at and booking.expires_at < utc_now():
        raise HTTPException(
            status_code=422,
            detail="This booking expired before payment cleared. Please re-book.",
        )

    booking.status = SessionBookingStatus.CONFIRMED
    booking.confirmed_at = utc_now()
    if confirm_in.payment_intent_id is not None:
        booking.payment_intent_id = confirm_in.payment_intent_id
    if confirm_in.wallet_transaction_id is not None:
        booking.wallet_transaction_id = confirm_in.wallet_transaction_id
    await db.commit()
    await db.refresh(booking)
    return booking


# ---------------------------------------------------------------------------
# Member or admin: cancel
# ---------------------------------------------------------------------------


@router.post(
    "/sessions/bookings/{booking_id}/cancel",
    response_model=SessionBookingResponse,
)
async def cancel_booking(
    booking_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Cancel a booking before the session starts.

    Refund policy (per A1 Phase 3.3 product decision):

    * Members can only cancel their own bookings.
    * Refund is issued in Bubbles to the member's wallet, NOT to the
      original card. Avoids transaction-fee reversal costs and keeps
      cancellations from being a platform loss. Members keep spending
      power and are more likely to rebook later.
    * The FULL ``fee_amount_kobo`` is refunded as Bubbles (no haircut
      for the platform's transaction fee — that's already sunk on the
      original payment and not recoverable). Refund amount conversion:
      ``kobo_to_bubbles(booking.fee_amount_kobo)``.
    * Cancellations after the session has started are refused; the
      nightly NO_SHOW sweep will produce ``AttendanceRecord(status=ABSENT,
      booking_id=<>)`` for those.
    * Cash refunds to the original payment method are admin-only and
      out of scope for this endpoint.
    """
    booking = (
        await db.execute(select(SessionBooking).where(SessionBooking.id == booking_id))
    ).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.member_auth_id != current_user.user_id:
        raise HTTPException(
            status_code=403, detail="You can only cancel your own bookings."
        )
    if booking.status in (
        SessionBookingStatus.CANCELLED,
        SessionBookingStatus.EXPIRED,
    ):
        return booking  # idempotent

    if booking.status == SessionBookingStatus.CONFIRMED:
        # Refuse if the session has already started.
        session = (
            await db.execute(select(Session).where(Session.id == booking.session_id))
        ).scalar_one_or_none()
        if session is not None and session.starts_at <= utc_now():
            raise HTTPException(
                status_code=422,
                detail=(
                    "Cannot cancel a booking after its session has started. "
                    "The booking will be marked NO_SHOW if no attendance is "
                    "recorded."
                ),
            )

    was_confirmed = booking.status == SessionBookingStatus.CONFIRMED
    booking.status = SessionBookingStatus.CANCELLED
    booking.cancelled_at = utc_now()
    await db.commit()
    await db.refresh(booking)

    # Bubble refund — only for CONFIRMED bookings with a non-zero fee.
    # Best-effort: if the wallet call fails we log and let the user
    # contact support; the booking is already cancelled regardless.
    if was_confirmed and booking.fee_amount_kobo > 0:
        try:
            await credit_member_wallet(
                booking.member_auth_id,
                amount=kobo_to_bubbles(booking.fee_amount_kobo),
                idempotency_key=f"booking-refund-{booking.id}",
                description=f"Refund for cancelled booking {booking.id}",
                calling_service="sessions",
                transaction_type="refund",
                reference_type="session_booking",
                reference_id=str(booking.id),
            )
        except httpx.HTTPError as exc:
            logger.error(
                "Bubble refund failed for booking %s: %s",
                booking.id,
                exc,
            )

    return booking


# ---------------------------------------------------------------------------
# Admin: who's paid for a session
# ---------------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/bookings",
    response_model=List[SessionBookingResponse],
)
async def list_session_bookings(
    session_id: uuid.UUID,
    status_filter: Optional[SessionBookingStatus] = None,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin: list bookings for a session.

    Defaults to CONFIRMED only (i.e. "who's paid"). Pass
    ``?status_filter=pending`` (or other values) to see other states.
    Combined with the existing attendance pool-list endpoint, this is
    how admins reconcile expected attendance vs actual at session time.
    """
    query = select(SessionBooking).where(SessionBooking.session_id == session_id)
    query = query.where(
        SessionBooking.status == (status_filter or SessionBookingStatus.CONFIRMED)
    )
    query = query.order_by(SessionBooking.booked_at.asc())
    rows = (await db.execute(query)).scalars().all()
    return rows
