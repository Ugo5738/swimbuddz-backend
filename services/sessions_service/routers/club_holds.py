"""Reserve every sold future swim atomically, before payment initialization."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select, text

from libs.auth.dependencies import require_service_role
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.sessions_service.models import (
    ClubSessionHold,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionStatus,
    SessionType,
)
from services.sessions_service.services.booking_capacity import assert_booking_capacity
from services.sessions_service.services.club_holds import live_club_hold

router = APIRouter(
    prefix="/internal/sessions/club-holds", dependencies=[Depends(require_service_role)]
)


class HoldPlan(BaseModel):
    plan_version_id: uuid.UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    session_ids: list[uuid.UUID] = Field(min_length=1, max_length=260)


class ReserveClubHolds(BaseModel):
    application_id: uuid.UUID
    club_id: uuid.UUID
    member_id: uuid.UUID
    payment_reference: str = Field(min_length=1, max_length=128)
    expires_at: AwareDatetime
    plans: list[HoldPlan] = Field(min_length=1, max_length=8)


class HoldAction(BaseModel):
    application_id: uuid.UUID
    payment_reference: str = Field(min_length=1, max_length=128)
    closure_evidence: str | None = Field(default=None, min_length=10, max_length=500)


async def lock_reference(db, reference):
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"club-holds:{reference}"},
    )


@router.post("")
async def reserve_holds(body: ReserveClubHolds, db=Depends(get_async_db)):
    now = utc_now()
    if body.expires_at <= now:
        raise HTTPException(409, "Club checkout reservation has expired")
    await lock_reference(db, body.payment_reference)
    plans = {sid: plan for plan in body.plans for sid in plan.session_ids}
    sessions = list(
        (
            await db.execute(
                select(Session)
                .where(Session.id.in_(plans))
                .order_by(Session.id)
                .with_for_update()
            )
        ).scalars()
    )
    if len(sessions) != len(plans):
        raise HTTPException(409, "A selected quarter includes a missing swim")
    existing = {
        row.session_id: row
        for row in (
            await db.execute(
                select(ClubSessionHold)
                .where(ClubSessionHold.payment_reference == body.payment_reference)
                .with_for_update()
            )
        ).scalars()
    }
    if any(
        row.application_id != body.application_id
        or row.member_id != body.member_id
        or row.session_id not in plans
        for row in existing.values()
    ):
        raise HTTPException(409, "Checkout reference belongs to another selection")
    held = []
    for session in sessions:
        plan = plans[session.id]
        if (
            session.club_id != body.club_id
            or session.session_type != SessionType.CLUB
            or session.club_access_mode != "plan_included"
        ):
            raise HTTPException(
                409, "Selected swim does not belong to this Club quarter"
            )
        if session.starts_at <= now:
            continue
        if (
            not plan.starts_at <= session.starts_at < plan.ends_at
            or session.status != SessionStatus.SCHEDULED
        ):
            raise HTTPException(
                409, "A selected future swim is not available in its published quarter"
            )
        other = await db.scalar(
            select(ClubSessionHold.id)
            .where(
                ClubSessionHold.session_id == session.id,
                ClubSessionHold.member_id == body.member_id,
                ClubSessionHold.payment_reference != body.payment_reference,
                live_club_hold(now),
            )
            .limit(1)
        )
        if other:
            raise HTTPException(
                409, "This member already has another checkout holding a selected swim"
            )
        booking = await db.scalar(
            select(SessionBooking).where(
                SessionBooking.session_id == session.id,
                SessionBooking.member_id == body.member_id,
            )
        )
        if (
            booking
            and booking.status == SessionBookingStatus.PENDING
            and (booking.expires_at is None or booking.expires_at > now)
        ):
            raise HTTPException(
                409,
                "Finish the existing session booking payment before buying this quarter",
            )
        row = existing.get(session.id)
        if row and row.status in {"protected", "consumed"}:
            held.append(str(session.id))
            continue
        await assert_booking_capacity(
            db,
            session=session,
            member_id=body.member_id,
            new_party_size=booking.party_size
            if booking and booking.status == SessionBookingStatus.CONFIRMED
            else 1,
        )
        if row is None:
            row = ClubSessionHold(
                session_id=session.id,
                application_id=body.application_id,
                club_id=body.club_id,
                member_id=body.member_id,
                plan_version_id=plan.plan_version_id,
                payment_reference=body.payment_reference,
            )
            db.add(row)
        row.status, row.expires_at = "active", body.expires_at
        held.append(str(session.id))
    await db.commit()
    return {"session_ids": held, "expires_at": body.expires_at}


async def action_holds(db, body, *, protect=False):
    await lock_reference(db, body.payment_reference)
    rows = list(
        (
            await db.execute(
                select(ClubSessionHold)
                .where(
                    ClubSessionHold.payment_reference == body.payment_reference,
                    ClubSessionHold.application_id == body.application_id,
                )
                .order_by(ClubSessionHold.session_id)
            )
        ).scalars()
    )
    sessions = list(
        (
            await db.execute(
                select(Session)
                .where(Session.id.in_([row.session_id for row in rows]))
                .order_by(Session.id)
                .with_for_update()
            )
        ).scalars()
    )
    by_id = {session.id: session for session in sessions}
    for row in rows:
        await db.refresh(row)
        if row.status == "consumed":
            continue
        if protect:
            if row.status == "released":
                raise HTTPException(
                    409, "Club seats were released; start a fresh checkout"
                )
            if row.status != "protected":
                await assert_booking_capacity(
                    db,
                    session=by_id[row.session_id],
                    member_id=row.member_id,
                    new_party_size=1,
                )
                row.status = "protected"
        elif row.status == "protected" and not body.closure_evidence:
            raise HTTPException(
                409,
                "A payment can still arrive for these Club seats; provider reconciliation is required",
            )
        else:
            row.status = "released"
    await db.commit()
    return {
        "status": "protected" if protect else "released",
        "session_ids": [str(row.session_id) for row in rows],
    }


@router.post("/protect")
async def protect_holds(body: HoldAction, db=Depends(get_async_db)):
    return await action_holds(db, body, protect=True)


@router.post("/release")
async def release_holds(body: HoldAction, db=Depends(get_async_db)):
    return await action_holds(db, body)
