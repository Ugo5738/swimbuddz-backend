"""Admin SessionParticipant operations."""

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import _service_role_jwt, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.sessions_service.models import Session, SessionParticipant, SessionRate
from services.sessions_service.schemas.participant import (
    AdminGuestWalkInCreate,
    AdminGuestWalkInResponse,
    SessionParticipantResponse,
    WalkInPaymentReconcile,
)
from services.sessions_service.services.participants import (
    create_guest_walk_in,
    ensure_session_participants,
)

router = APIRouter(tags=["session-participants"])


async def _record_participant_attendance(
    session_id: uuid.UUID,
    participant_id: uuid.UUID,
    notes: str | None,
) -> bool:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{settings.ATTENDANCE_SERVICE_URL}"
                f"/attendance/sessions/{session_id}/attendance/participant",
                json={
                    "participant_id": str(participant_id),
                    "status": "present",
                    "notes": notes or "Admin guest walk-in",
                },
                headers={"Authorization": f"Bearer {_service_role_jwt('sessions')}"},
            )
        return response.status_code < 400
    except httpx.HTTPError:
        return False


@router.post(
    "/admin/sessions/{session_id}/walk-ins",
    response_model=AdminGuestWalkInResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_guest_walk_in(
    session_id: uuid.UUID,
    payload: AdminGuestWalkInCreate,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Record an unregistered person who physically attended a Session.

    This does not manufacture a Member account, pretend a waiver was accepted,
    or create a paid GuestPass. Attendance and payment remain separate facts.
    """
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if getattr(session.status, "value", session.status) in {"draft", "cancelled"}:
        raise HTTPException(
            status_code=409, detail="Walk-ins cannot be added to this session"
        )

    guest_rate = (
        (
            await db.execute(
                select(SessionRate)
                .where(
                    SessionRate.session_id == session.id,
                    SessionRate.audience == "guest",
                    SessionRate.is_active.is_(True),
                    SessionRate.access_source.is_(None),
                )
                .order_by(SessionRate.priority, SessionRate.id)
            )
        )
        .scalars()
        .first()
    )
    default_fee = (
        int(guest_rate.amount_kobo)
        if guest_rate is not None
        else int(session.guest_fee_kobo or session.pool_fee or 0)
    )
    fee = (
        int(payload.fee_amount_kobo)
        if payload.fee_amount_kobo is not None
        else default_fee
    )
    if fee != default_fee and not (payload.fee_override_reason or "").strip():
        raise HTTPException(
            status_code=422,
            detail="Explain why this walk-in uses a fee different from the configured guest rate.",
        )
    payment_status = payload.payment_status or ("included" if fee == 0 else "unpaid")
    participant_notes = payload.notes
    if payload.fee_override_reason:
        override_note = f"Fee override: {payload.fee_override_reason.strip()}"
        participant_notes = "\n".join(
            part for part in [participant_notes, override_note] if part
        )

    participant = await create_guest_walk_in(
        db,
        session=session,
        full_name=payload.full_name,
        email=payload.email,
        phone=payload.phone,
        fee_amount_kobo=fee,
        payment_status=payment_status,
        payment_method=payload.payment_method,
        payment_reference=payload.payment_reference,
        waiver_status=payload.waiver_status,
        notes=participant_notes,
        created_by=admin.user_id,
    )
    await db.commit()
    await db.refresh(participant)

    attendance_recorded = await _record_participant_attendance(
        session.id,
        participant.id,
        participant_notes,
    )
    data = SessionParticipantResponse.model_validate(participant).model_dump()
    return AdminGuestWalkInResponse(
        **data,
        attendance_recorded=attendance_recorded,
    )


@router.get(
    "/admin/sessions/{session_id}/participants",
    response_model=list[SessionParticipantResponse],
)
async def list_session_participants(
    session_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    rows = await ensure_session_participants(db, session)
    await db.commit()
    return rows


@router.patch(
    "/admin/session-participants/{participant_id}/payment",
    response_model=SessionParticipantResponse,
)
async def reconcile_walk_in_payment(
    participant_id: uuid.UUID,
    payload: WalkInPaymentReconcile,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    participant = await db.get(SessionParticipant, participant_id)
    if participant is None:
        raise HTTPException(status_code=404, detail="Session participant not found")
    if participant.source != "walk_in":
        raise HTTPException(
            status_code=409,
            detail="Only admin-recorded walk-ins are reconciled through this endpoint",
        )

    participant.payment_status = payload.payment_status
    participant.payment_method = payload.payment_method
    participant.payment_reference = payload.payment_reference
    participant.paid_at = utc_now() if payload.payment_status == "paid" else None
    if payload.note:
        participant.notes = "\n".join(
            part for part in [participant.notes, payload.note.strip()] if part
        )
    await db.commit()
    await db.refresh(participant)
    return participant
