"""SessionBooking endpoints — member-facing + admin filter.

Routes (mounted under /sessions by app/main.py):

  POST /sessions/{session_id}/book               — member self-book (PENDING)
  POST /sessions/bookings/{booking_id}/confirm   — flip PENDING → CONFIRMED after payment
  POST /sessions/bookings/{booking_id}/cancel    — member or admin cancel
  POST /sessions/bookings/{booking_id}/refund-pool-fee — admin: refund pool fee → Bubbles (make-up)
  GET  /sessions/{session_id}/bookings           — admin: list CONFIRMED bookings for a session

The booking lifecycle is intent-only. Day-of attendance still goes through
attendance_service's sign-in flow — that's what creates the
``AttendanceRecord`` and links it back here via ``booking_id``.

See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md §C.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import _service_role_jwt, get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.currency import kobo_to_bubbles
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    check_cohort_enrollment,
    credit_member_wallet,
    debit_member_wallet,
    get_member_by_auth_id,
)
from libs.db.session import get_async_db
from services.sessions_service.models import (
    BookingChannel,
    BookingGuest,
    Session,
    SessionBooking,
    SessionBookingStatus,
)
from services.sessions_service.schemas import (
    AdminPoolFeeRefundRequest,
    AdminWalkInRequest,
    BookingConfirmRequest,
    RunningLateRequest,
    SessionBookingCreate,
    SessionBookingResponse,
    UnpaidBookingResponse,
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


async def _debit_booking_fee(
    *,
    member_auth_id: str,
    session_id: uuid.UUID,
    member_id: uuid.UUID,
    booked_at: datetime,
    fee_amount_kobo: int,
    session_title: str,
) -> Optional[uuid.UUID]:
    """Debit a member's wallet for a booking's pool fee.

    Returns the wallet transaction id, or ``None`` when there's no fee to
    charge (free session). Translates wallet errors into member-facing
    HTTP errors (402 insufficient, 403 inactive).

    The idempotency key includes ``booked_at`` so each fresh booking
    *attempt* debits independently — a brand-new booking, or a revived
    EXPIRED/CANCELLED row whose ``booked_at`` was reset, each get a
    distinct key — while a double-submit of the *same* attempt replays the
    same key and never double-charges.
    """
    if fee_amount_kobo <= 0:
        return None
    try:
        result_txn = await debit_member_wallet(
            member_auth_id,
            amount=kobo_to_bubbles(fee_amount_kobo),
            idempotency_key=(
                f"booking-fee-{session_id}-{member_id}-{int(booked_at.timestamp())}"
            ),
            description=f"Session booking — {session_title}",
            calling_service="sessions",
            transaction_type="purchase",
            reference_type="session_booking",
            reference_id=f"{session_id}",
        )
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
    txn = result_txn.get("transaction_id")
    return uuid.UUID(txn) if txn else None


# ---------------------------------------------------------------------------
# Guest booking helpers (slice 1b — see GUEST_AND_GROUP_BOOKING_DESIGN.md)
# ---------------------------------------------------------------------------


def _is_minor(dob, session_starts_at: datetime) -> bool:
    """True if the guest is under 18 on the session date."""
    on = session_starts_at.date()
    age = on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))
    return age < 18


def _validate_guest_policy(
    *,
    allows_guests: bool,
    max_guests: int,
    session_starts_at: datetime,
    guests: list,
) -> None:
    """Reject a guest list that breaks the session's guest policy or the
    safeguarding rules. Pure (primitives in) so it unit-tests without a DB.

    - the session must allow guests and the count must be within max_guests
    - every guest needs a name (Phase 1 named-guest flow)
    - a minor (DOB < 18 at the session) needs guardian name + phone + waiver
    """
    if not guests:
        return
    if not allows_guests or max_guests <= 0:
        raise HTTPException(
            status_code=422, detail="This session does not accept guests."
        )
    if len(guests) > max_guests:
        raise HTTPException(
            status_code=422,
            detail=f"This session allows at most {max_guests} guest(s) per booking.",
        )
    for g in guests:
        name = (g.full_name or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Each guest must have a name.")
        if g.date_of_birth is not None and _is_minor(
            g.date_of_birth, session_starts_at
        ):
            if not (
                (g.guardian_name or "").strip()
                and (g.guardian_phone or "").strip()
                and g.waiver_accepted
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Guest '{name}' is a minor — guardian name, guardian "
                        f"phone, and an accepted waiver are required."
                    ),
                )


async def _assert_capacity(
    db: AsyncSession,
    *,
    session: Session,
    member_id: uuid.UUID,
    new_party_size: int,
) -> None:
    """Reject a booking that would exceed the session's head-count capacity.

    Sums ``party_size`` over active bookings (CONFIRMED + unexpired PENDING),
    excluding this member's own row so a re-book / party-size change doesn't
    double-count their seat. A ``FOR UPDATE`` lock on the session row serialises
    concurrent bookings so two members can't both grab the last seats. The lock
    is held until the request commits — which, on the Bubbles path, spans the
    wallet debit; per-session contention is low, so that's an acceptable trade.
    """
    await db.execute(
        select(Session.id).where(Session.id == session.id).with_for_update()
    )
    now = utc_now()
    used = (
        await db.execute(
            select(func.coalesce(func.sum(SessionBooking.party_size), 0)).where(
                SessionBooking.session_id == session.id,
                SessionBooking.member_id != member_id,
                or_(
                    SessionBooking.status == SessionBookingStatus.CONFIRMED,
                    and_(
                        SessionBooking.status == SessionBookingStatus.PENDING,
                        or_(
                            SessionBooking.expires_at.is_(None),
                            SessionBooking.expires_at > now,
                        ),
                    ),
                ),
            )
        )
    ).scalar_one()
    if int(used) + new_party_size > session.capacity:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This session is full — {int(used)}/{session.capacity} seats "
                f"taken and you need {new_party_size}."
            ),
        )


async def _replace_guests(
    db: AsyncSession, booking_id: uuid.UUID, guests: list
) -> None:
    """Replace a booking's guest rows with the incoming set (idempotent on
    re-book; a no-op delete when there are none)."""
    await db.execute(delete(BookingGuest).where(BookingGuest.booking_id == booking_id))
    for g in guests:
        db.add(
            BookingGuest(
                booking_id=booking_id,
                full_name=(g.full_name or None),
                phone=(g.phone or None),
                intent=getattr(g.intent, "value", str(g.intent)),
                date_of_birth=g.date_of_birth,
                guardian_name=(g.guardian_name or None),
                guardian_phone=(g.guardian_phone or None),
                waiver_accepted_at=utc_now() if g.waiver_accepted else None,
            )
        )


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
    """Book a session as the authenticated member.

    Reconciles against the single existing booking row for this
    (session, member), if any (a unique constraint forbids a second row):

    * CONFIRMED already → returned unchanged (idempotent; never re-charged).
    * PENDING (payment in flight / abandoned) or dead (EXPIRED / CANCELLED)
      → driven forward by *this* attempt rather than rejected. A dead row is
      revived with its stale payment linkage cleared.

    Then, for that row (existing or freshly created):

    * ``pay_with_bubbles`` (or a free session) → debit the wallet for the
      pool fee, if any, and flip to CONFIRMED in one transaction.
    * otherwise → PENDING with a 15-minute TTL; the frontend pays via
      Paystack and confirms on verify (the sweep retires it if payment
      never clears).
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

    # Cohort sessions are auto-rostered from academy enrollments. Block
    # ad-hoc self-bookings from members who aren't enrolled in the session's
    # cohort — otherwise they end up paying for and "attending" cohorts
    # they aren't part of. Admin walk-in still bypasses this check so
    # coaches can admit legitimate drop-ins.
    if session.cohort_id is not None:
        try:
            check = await check_cohort_enrollment(
                cohort_id=str(session.cohort_id),
                member_id=str(member_id),
                calling_service="sessions",
            )
        except httpx.HTTPError as e:
            logger.warning(
                "check_cohort_enrollment failed for session=%s member=%s: %s",
                session_id,
                member_id,
                e,
            )
            raise HTTPException(
                status_code=503,
                detail="Could not verify cohort enrollment. Please try again.",
            )
        if not check or not check.get("enrolled"):
            raise HTTPException(
                status_code=403,
                detail=(
                    "This session is restricted to members enrolled in its "
                    "academy cohort. Enroll in the cohort first to book."
                ),
            )

    now = utc_now()

    # At most one booking row per (session, member) exists — enforced by the
    # uq_session_bookings_session_member constraint — so there's a single row
    # to reconcile against this incoming attempt.
    existing = (
        await db.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == session_id,
                SessionBooking.member_id == member_id,
            )
        )
    ).scalar_one_or_none()

    # A CONFIRMED booking is returned unchanged (idempotent; never re-charged,
    # guests untouched) before any validation or capacity lock runs.
    if existing is not None and existing.status == SessionBookingStatus.CONFIRMED:
        return existing

    # Server-authoritative head count + fee: the member's own slot plus any
    # named guests (D1/D3); fee is pool_fee × heads (D8) — the client-sent
    # fee_amount_kobo is ignored. Guest eligibility + capacity are checked
    # before any wallet debit so we never charge and then reject.
    party_size = 1 + len(booking_in.guests)
    fee_kobo = int(session.pool_fee or 0) * party_size
    _validate_guest_policy(
        allows_guests=session.allows_guests,
        max_guests=session.max_guests_per_booking,
        session_starts_at=session.starts_at,
        guests=booking_in.guests,
    )
    await _assert_capacity(
        db, session=session, member_id=member_id, new_party_size=party_size
    )

    if existing is not None:
        # PENDING (payment in flight / abandoned online attempt) or dead
        # (EXPIRED / CANCELLED). The unique constraint forbids a second row,
        # so drive *this* row forward instead of rejecting the re-book.
        # Reviving a dead row clears its stale payment linkage so a prior
        # intent/txn is never reused and the debit idempotency window (keyed
        # on booked_at) reopens for the fresh attempt.
        if existing.status in (
            SessionBookingStatus.EXPIRED,
            SessionBookingStatus.CANCELLED,
        ):
            existing.payment_intent_id = None
            existing.wallet_transaction_id = None
            existing.confirmed_at = None
            existing.cancelled_at = None
            existing.channel = BookingChannel.MEMBER_SELF
            existing.booked_at = now

        existing.party_size = party_size
        existing.fee_amount_kobo = fee_kobo
        if booking_in.notes is not None:
            existing.notes = booking_in.notes

        if booking_in.pay_with_bubbles:
            # Bubbles / free → debit (if any fee) and confirm this row. This
            # is the core fix: an existing PENDING row used to short-circuit
            # with `return existing` *before* the debit ran, so re-booking
            # with Bubbles silently left the booking PENDING and unpaid.
            existing.wallet_transaction_id = await _debit_booking_fee(
                member_auth_id=member_auth_id,
                session_id=session_id,
                member_id=member_id,
                booked_at=existing.booked_at,
                fee_amount_kobo=fee_kobo,
                session_title=session.title,
            )
            existing.status = SessionBookingStatus.CONFIRMED
            existing.confirmed_at = now
            existing.expires_at = None
        else:
            # Paystack → (re)open the PENDING window; the frontend creates a
            # fresh payment intent against this booking id and confirms on
            # verify. Refreshing expires_at stops the sweep retiring the row
            # mid-payment.
            existing.status = SessionBookingStatus.PENDING
            existing.expires_at = now + timedelta(minutes=PENDING_TTL_MINUTES)

        await _replace_guests(db, existing.id, booking_in.guests)
        await db.commit()
        await db.refresh(existing)
        return existing

    # No prior booking for this (session, member) → create one.
    #
    # Fast path: free session OR member elected to pay full Bubbles. Mirrors
    # the one-click sign-in UX: debit wallet (if non-zero fee) → CONFIRMED.
    if booking_in.pay_with_bubbles:
        wallet_txn_id = await _debit_booking_fee(
            member_auth_id=member_auth_id,
            session_id=session_id,
            member_id=member_id,
            booked_at=now,
            fee_amount_kobo=fee_kobo,
            session_title=session.title,
        )
        booking = SessionBooking(
            session_id=session_id,
            member_id=member_id,
            member_auth_id=member_auth_id,
            status=SessionBookingStatus.CONFIRMED,
            channel=BookingChannel.MEMBER_SELF,
            party_size=party_size,
            fee_amount_kobo=fee_kobo,
            notes=booking_in.notes,
            wallet_transaction_id=wallet_txn_id,
            booked_at=now,
            confirmed_at=now,
        )
        db.add(booking)
        await db.flush()
        await _replace_guests(db, booking.id, booking_in.guests)
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
        party_size=party_size,
        fee_amount_kobo=fee_kobo,
        notes=booking_in.notes,
        booked_at=now,
        expires_at=now + timedelta(minutes=PENDING_TTL_MINUTES),
    )
    db.add(booking)
    await db.flush()
    await _replace_guests(db, booking.id, booking_in.guests)
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
# Member: list unpaid bookings (outstanding pool fees)
# ---------------------------------------------------------------------------


@router.get(
    "/sessions/bookings/me/unpaid",
    response_model=List[UnpaidBookingResponse],
)
async def list_my_unpaid_bookings(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List the current member's CONFIRMED bookings with an outstanding fee.

    A booking is "unpaid" when:
      - status = CONFIRMED
      - fee_amount_kobo > 0
      - no payment_intent_id linked (no Paystack payment recorded)
      - no wallet_transaction_id linked (not paid via Bubbles)

    The typical source is admin walk-in records: the coach marked a member
    present at the pool, but the member hadn't booked online. The billing
    UI surfaces this list so the member can pay the pool fee after-the-fact
    via a generated Paystack link (POST /api/v1/payments/intents with
    purpose=session_booking, payment_metadata.booking_id=<this id>).
    """
    member_id, _ = await _resolve_member_for_user(current_user)
    query = (
        select(
            SessionBooking.id,
            SessionBooking.session_id,
            Session.title.label("session_title"),
            Session.starts_at.label("session_starts_at"),
            Session.ends_at.label("session_ends_at"),
            SessionBooking.fee_amount_kobo,
            SessionBooking.channel,
            SessionBooking.booked_at,
            SessionBooking.notes,
        )
        .join(Session, Session.id == SessionBooking.session_id)
        .where(
            SessionBooking.member_id == member_id,
            SessionBooking.status == SessionBookingStatus.CONFIRMED,
            SessionBooking.fee_amount_kobo > 0,
            SessionBooking.payment_intent_id.is_(None),
            SessionBooking.wallet_transaction_id.is_(None),
        )
        .order_by(SessionBooking.booked_at.desc())
    )
    rows = (await db.execute(query)).mappings().all()
    # Mappings → UnpaidBookingResponse via Pydantic (from_attributes works on
    # dict-like rows too).
    return [UnpaidBookingResponse(**dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Admin: walk-in booking
# ---------------------------------------------------------------------------


async def _record_walk_in_attendance(
    session_id: uuid.UUID, member_id: uuid.UUID
) -> None:
    """Best-effort: mark a walk-in member PRESENT in attendance_service.

    A walk-in means the member is physically at the pool, so attendance is
    recorded immediately — this is what makes walk-ins count in quarterly
    reports and stops the nightly NO_SHOW sweep from later marking them ABSENT.
    The public attendance endpoint is an idempotent upsert keyed on
    (session, member); it links the CONFIRMED booking and trusts it for access,
    so repeat calls are safe. A transient attendance-service failure must not
    fail the walk-in record itself, so errors are logged rather than raised.
    """
    from libs.common.config import get_settings

    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.ATTENDANCE_SERVICE_URL}"
                f"/attendance/sessions/{session_id}/attendance/public",
                json={
                    "member_id": str(member_id),
                    # attendance_service AttendanceStatus.PRESENT / AttendanceRole.SWIMMER
                    # wire values (lowercase). Sent as literals — cross-service
                    # models must not be imported across service boundaries.
                    "status": "present",
                    "role": "swimmer",
                    "notes": "Admin walk-in",
                },
                headers={"Authorization": f"Bearer {_service_role_jwt('sessions')}"},
            )
        if resp.status_code >= 400:
            logger.warning(
                "Walk-in attendance not recorded (session=%s member=%s): %s %s",
                session_id,
                member_id,
                resp.status_code,
                resp.text,
            )
    except Exception as exc:  # noqa: BLE001 - best-effort side-effect
        logger.warning(
            "Walk-in attendance call errored (session=%s member=%s): %s",
            session_id,
            member_id,
            exc,
        )


@router.post(
    "/sessions/{session_id}/admin/walk-in",
    response_model=SessionBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_walk_in_booking(
    session_id: uuid.UUID,
    payload: AdminWalkInRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin creates a CONFIRMED booking for a member who showed up without
    pre-booking online. Used by the attendance UI's "Mark walk-in" action.

    Behavior:
      - Looks up the session to default ``fee_amount_kobo`` to the session's
        own ``pool_fee`` when the caller didn't specify one.
      - Idempotent: if a PENDING or CONFIRMED booking already exists for
        ``(session_id, member_id)``, returns it instead of creating a new one.
        Cancelled/expired bookings raise 409 (admin must investigate).
      - Channel is hard-coded to ``ADMIN`` so the row is distinguishable from
        member-self bookings in reporting.
      - Coach payouts pick this up like any other booking — paying happens
        when an attendance row records Present/Late for this booking.
    """
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Resolve the member to fill member_auth_id (the booking row needs both).
    # We can't use _resolve_member_for_user here — the admin isn't the
    # booking subject — so look up by member id directly via members-service.
    from libs.common.service_client import get_member_by_id

    member = await get_member_by_id(str(payload.member_id), calling_service="sessions")
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    member_auth_id = member.get("auth_id")
    if not member_auth_id:
        raise HTTPException(
            status_code=422,
            detail="Member is missing auth_id — cannot create a booking.",
        )

    fee_kobo = (
        payload.fee_amount_kobo
        if payload.fee_amount_kobo is not None
        else int(session.pool_fee or 0)
    )

    # Idempotency: return existing PENDING/CONFIRMED if any.
    existing = (
        await db.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == session_id,
                SessionBooking.member_id == payload.member_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status in (
            SessionBookingStatus.PENDING,
            SessionBookingStatus.CONFIRMED,
        ):
            # Upgrade PENDING to CONFIRMED if needed — admin walk-in implies
            # payment has happened (the member is physically present).
            if existing.status == SessionBookingStatus.PENDING:
                existing.status = SessionBookingStatus.CONFIRMED
                existing.confirmed_at = utc_now()
                existing.channel = BookingChannel.ADMIN
                if not existing.notes and payload.notes:
                    existing.notes = payload.notes
                await db.commit()
                await db.refresh(existing)
            await _record_walk_in_attendance(session_id, payload.member_id)
            return existing
        raise HTTPException(
            status_code=409,
            detail=(
                f"A prior booking for this (session, member) exists with "
                f"status={existing.status.value}. Resolve it before recording "
                f"a fresh walk-in."
            ),
        )

    now = utc_now()
    booking = SessionBooking(
        session_id=session_id,
        member_id=payload.member_id,
        member_auth_id=member_auth_id,
        status=SessionBookingStatus.CONFIRMED,
        channel=BookingChannel.ADMIN,
        fee_amount_kobo=fee_kobo,
        notes=payload.notes,
        booked_at=now,
        confirmed_at=now,
    )
    db.add(booking)
    await db.commit()
    await db.refresh(booking)
    logger.info(
        "Admin %s recorded walk-in booking %s for session %s, member %s, fee %s kobo",
        admin.email or admin.user_id,
        booking.id,
        session_id,
        payload.member_id,
        fee_kobo,
    )
    await _record_walk_in_attendance(session_id, payload.member_id)
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
# Admin: refund a booking's pool fee (rain-out / make-up)
# ---------------------------------------------------------------------------

# Audit + idempotency marker appended to booking.notes when an admin refunds
# the pool fee. Presence of the prefix means "already refunded" — short-circuits
# a second call and lets the UI show the refund. The wallet credit is *also*
# idempotent (key booking-refund-<id>), so a double-call can't double-credit.
POOL_REFUND_PREFIX = "[pool_fee_refunded_at:"


def _has_pool_refund_marker(notes: Optional[str]) -> bool:
    return bool(notes) and POOL_REFUND_PREFIX in notes


def _with_pool_refund_marker(notes: Optional[str], now: datetime, reason: str) -> str:
    # Appended (not prepended) so any leading self-excuse / running-late
    # sentinel that other code detects with startswith() stays at position 0.
    marker = f"{POOL_REFUND_PREFIX}{now.isoformat()}] {reason}".strip()
    return f"{notes}\n{marker}".strip() if notes else marker


@router.post(
    "/sessions/bookings/{booking_id}/refund-pool-fee",
    response_model=SessionBookingResponse,
)
async def admin_refund_pool_fee(
    booking_id: uuid.UUID,
    payload: AdminPoolFeeRefundRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin: refund a booking's per-session pool fee to the member's Bubbles.

    The make-up case: a learner paid the ~₦3,500 pool fee, couldn't attend
    (rain-out / excused / marked ABSENT) and is owed it back so it funds the
    make-up session — otherwise they'd pay the pool fee twice.

    Why a dedicated endpoint (not ``/cancel`` or the Adjust-Bubbles tool):
      - ``/cancel`` is member-only and refuses once the session has started; a
        rain-out is marked ABSENT *during/after* the session, so it can't apply.
      - The "Adjust Bubbles" admin tool posts a generic ``admin_adjustment`` the
        ledger SKIPS — the refund would be invisible and the pool-fee revenue
        double-counted once the make-up is rebooked.
      - This routes through the **accounted** ``session_booking`` refund path
        (``transaction_type=refund``), so the ledger reverses
        ``revenue_club_session`` and restores the Bubble liability. The make-up
        rebook re-recognises it — revenue lands once, for the delivered session.

    The booking is **not** cancelled — it stays (with its ABSENT/EXCUSED
    attendance row) as the audit trail. Idempotent per booking (notes marker +
    the wallet's shared ``booking-refund-<id>`` key). Phase 1 make-up *confirm*
    will call this same primitive automatically; until then it's an admin tap.
    """
    booking = (
        await db.execute(select(SessionBooking).where(SessionBooking.id == booking_id))
    ).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status != SessionBookingStatus.CONFIRMED:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Only a CONFIRMED booking can have its pool fee refunded "
                f"(this one is {booking.status.value}). A cancelled booking was "
                f"already refunded via /cancel."
            ),
        )
    if booking.fee_amount_kobo <= 0:
        raise HTTPException(
            status_code=422, detail="This booking has no pool fee to refund."
        )
    # Don't hand out money for a seat that was never paid (e.g. an unpaid
    # admin walk-in — CONFIRMED but no payment intent or wallet debit).
    if booking.wallet_transaction_id is None and booking.payment_intent_id is None:
        raise HTTPException(
            status_code=422,
            detail="This booking's pool fee hasn't been paid yet — nothing to refund.",
        )
    # Idempotent: already refunded → return unchanged.
    if _has_pool_refund_marker(booking.notes):
        return booking

    # Per-head (partial) refund: scale by heads when requested, else refund the
    # whole booking fee. fee_amount_kobo is pool_fee × party_size, so one head =
    # fee_amount_kobo // party_size. Refunds the no-show heads in a single call;
    # the notes marker keeps it once-per-booking (D4 / O3).
    refund_kobo = booking.fee_amount_kobo
    if payload.refund_heads is not None:
        if payload.refund_heads > booking.party_size:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Cannot refund {payload.refund_heads} head(s) — this booking "
                    f"covers {booking.party_size}."
                ),
            )
        per_head = booking.fee_amount_kobo // booking.party_size
        refund_kobo = per_head * payload.refund_heads

    try:
        await credit_member_wallet(
            booking.member_auth_id,
            amount=kobo_to_bubbles(refund_kobo),
            idempotency_key=f"booking-refund-{booking.id}",
            description=(
                f"Pool-fee refund (admin) for booking {booking.id}: {payload.reason}"
            ),
            calling_service="sessions",
            transaction_type="refund",
            reference_type="session_booking",
            reference_id=str(booking.id),
        )
    except httpx.HTTPError as exc:
        logger.error("Admin pool-fee refund failed for booking %s: %s", booking.id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Refund could not be issued to the wallet. Please retry.",
        )

    now = utc_now()
    booking.notes = _with_pool_refund_marker(booking.notes, now, payload.reason)
    await db.commit()
    await db.refresh(booking)
    logger.info(
        "Admin %s refunded pool fee (%s kobo) for booking %s, member %s: %s",
        admin.email or admin.user_id,
        refund_kobo,
        booking.id,
        booking.member_id,
        payload.reason,
    )
    return booking


# ---------------------------------------------------------------------------
# Member: self-report — "I can't make it" + "I'll be late"
# ---------------------------------------------------------------------------

# Cancellation cutoff for self-excuse. Members must signal "I can't make it"
# at least this many hours before session start to qualify for the make-up
# (cohort) or refund (non-cohort) workflow. Late cancels still record the
# absence but skip the make-up obligation downstream. Hardcoded for now —
# can be promoted to a per-program field if/when ops needs differentiation.
CANCELLATION_CUTOFF_HOURS = 24

# Sentinel marker stored in booking.notes to indicate a member has signalled
# they will arrive late. Coach sees this on the attendance roster. Kept as a
# string prefix (rather than a dedicated column) so this feature ships
# without an alembic migration.
LATE_FLAG_PREFIX = "[running_late_at:"
LATE_FLAG_END = "]"


def _has_late_flag(notes: Optional[str]) -> bool:
    return bool(notes) and notes.startswith(LATE_FLAG_PREFIX)


def _strip_late_flag(notes: Optional[str]) -> str:
    """Return ``notes`` with the running-late sentinel removed, if present."""
    if not notes or not notes.startswith(LATE_FLAG_PREFIX):
        return notes or ""
    end_idx = notes.find(LATE_FLAG_END)
    if end_idx == -1:
        return notes
    rest = notes[end_idx + 1 :].lstrip("\n ")
    return rest


def _with_late_flag(notes: Optional[str], now: datetime) -> str:
    base = _strip_late_flag(notes)
    flag = f"{LATE_FLAG_PREFIX}{now.isoformat()}{LATE_FLAG_END}"
    return f"{flag}\n{base}" if base else flag


@router.post(
    "/sessions/bookings/{booking_id}/excuse",
    response_model=SessionBookingResponse,
)
async def excuse_booking(
    booking_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Member self-excuses a booking — "I can't make it".

    Distinct from cancel:
      - For COHORT sessions, this endpoint moves no money. It creates an
        EXCUSED attendance record which the coach payout cron converts into a
        CohortMakeupObligation — the member gets a make-up session later. The
        *program* fee stays (a cohort commitment, not a per-session refundable
        purchase). The *per-session pool fee* the member paid is refunded to
        Bubbles separately — by an admin via the refund-pool-fee action, or
        automatically on make-up confirm (Phase 1) — so it funds the make-up.
      - For non-cohort sessions, callers should use the regular
        ``/cancel`` endpoint instead (which refunds in Bubbles). This
        endpoint rejects non-cohort bookings.

    Validates:
      - Booking belongs to the caller.
      - Session is in the future AND at least ``CANCELLATION_CUTOFF_HOURS``
        away. Late self-excuses get a 422 — admin can still override via
        the coach-mark endpoint if circumstances warrant.
    """
    booking = (
        await db.execute(select(SessionBooking).where(SessionBooking.id == booking_id))
    ).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.member_auth_id != current_user.user_id:
        raise HTTPException(
            status_code=403, detail="You can only excuse your own bookings."
        )
    if booking.status != SessionBookingStatus.CONFIRMED:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot excuse a booking with status={booking.status.value}. "
                f"Only confirmed bookings can be self-excused."
            ),
        )

    session = (
        await db.execute(select(Session).where(Session.id == booking.session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Underlying session not found")

    if session.cohort_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "This endpoint is for cohort sessions only. For community "
                "or club sessions, cancel the booking to get a Bubbles refund."
            ),
        )

    now = utc_now()
    if session.starts_at <= now:
        raise HTTPException(
            status_code=422,
            detail="Cannot excuse a booking after the session has started.",
        )
    hours_to_session = (session.starts_at - now).total_seconds() / 3600
    if hours_to_session < CANCELLATION_CUTOFF_HOURS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Self-excuse must be requested at least "
                f"{CANCELLATION_CUTOFF_HOURS} hours before the session "
                f"({hours_to_session:.1f}h remaining). Contact an admin if "
                f"there's a genuine emergency."
            ),
        )

    # Create the EXCUSED attendance record via attendance-service. The
    # coach-payout cron picks this up on its next run and materializes a
    # CohortMakeupObligation downstream — same path as a coach marking the
    # student EXCUSED in person.
    from libs.auth.dependencies import _service_role_jwt
    from libs.common.config import get_settings

    settings = get_settings()
    headers = {"Authorization": f"Bearer {_service_role_jwt('sessions')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.ATTENDANCE_SERVICE_URL}/attendance/sessions/{session.id}/attendance/public",
            json={
                "member_id": str(booking.member_id),
                "status": "excused",
                "role": "swimmer",
                "notes": "Self-excused via member app",
            },
            headers=headers,
        )
        if resp.status_code >= 400:
            logger.error(
                "Failed to create EXCUSED attendance for booking %s: %s %s",
                booking.id,
                resp.status_code,
                resp.text,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to record self-excuse. Please try again.",
            )

    # Append an audit hint to the booking notes so the coach can see why
    # the EXCUSED row appeared. Doesn't change booking status — the booking
    # remains CONFIRMED as the audit trail of "I paid for this seat, but
    # excused myself; coach owes me a make-up."
    excuse_note = f"[self_excused_at:{now.isoformat()}]"
    booking.notes = (
        f"{excuse_note}\n{_strip_late_flag(booking.notes)}".strip()
        if booking.notes
        else excuse_note
    )
    await db.commit()
    await db.refresh(booking)
    return booking


@router.post(
    "/sessions/bookings/{booking_id}/running-late",
    response_model=SessionBookingResponse,
)
async def set_running_late(
    booking_id: uuid.UUID,
    payload: RunningLateRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Member toggles their "I'll be late" flag on a booking.

    Pure signal — no business consequence. Coach sees the flag on the
    attendance roster so they know not to mark the member absent yet.
    Once the member arrives and is marked Present/Late, the flag is
    informational history.

    Stored as a sentinel prefix in ``booking.notes`` rather than its own
    column — keeps this feature shippable without a migration.
    """
    booking = (
        await db.execute(select(SessionBooking).where(SessionBooking.id == booking_id))
    ).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.member_auth_id != current_user.user_id:
        raise HTTPException(
            status_code=403, detail="You can only update your own bookings."
        )
    if booking.status != SessionBookingStatus.CONFIRMED:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot flag a {booking.status.value} booking.",
        )

    session = (
        await db.execute(select(Session).where(Session.id == booking.session_id))
    ).scalar_one_or_none()
    if session is not None and session.ends_at <= utc_now():
        raise HTTPException(
            status_code=422,
            detail="Cannot flag a session that has already ended.",
        )

    now = utc_now()
    if payload.running_late:
        booking.notes = _with_late_flag(booking.notes, now)
    else:
        booking.notes = _strip_late_flag(booking.notes) or None
    await db.commit()
    await db.refresh(booking)
    return booking


# ---------------------------------------------------------------------------
# Member: my bookings
# ---------------------------------------------------------------------------


@router.get(
    "/sessions/bookings/me",
    response_model=List[SessionBookingResponse],
)
async def list_my_bookings(
    status_filter: Optional[SessionBookingStatus] = None,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List the authenticated member's session bookings.

    Defaults to the *active* set — PENDING (payment in flight, still
    within the 15-min TTL) and CONFIRMED (paid, seat held). This is what
    the member's "Booked" tab consumes: the booking lifecycle is
    intent-only, so a confirmed booking never produces an AttendanceRecord
    until day-of sign-in — the Booked tab must read bookings directly,
    not attendance. Pass ``?status_filter=cancelled`` (etc.) to narrow to
    a single state.

    Registered before the admin ``/sessions/{session_id}/bookings`` route
    so the literal ``bookings/me`` path is matched first.
    """
    member_id, _auth_id = await _resolve_member_for_user(current_user)

    query = select(SessionBooking).where(SessionBooking.member_id == member_id)
    if status_filter is not None:
        query = query.where(SessionBooking.status == status_filter)
    else:
        query = query.where(
            SessionBooking.status.in_(
                [SessionBookingStatus.PENDING, SessionBookingStatus.CONFIRMED]
            )
        )
    query = query.order_by(SessionBooking.booked_at.desc())
    rows = (await db.execute(query)).scalars().all()
    return rows


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
