"""Guest sharing, individual approvals/reconciliation and funnel operations."""

import hashlib
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.sessions_service.models import (
    GuestBookingGrant,
    GuestLinkEvent,
    GuestPass,
    Session,
)
from services.sessions_service.schemas import SessionResponse
from services.sessions_service.schemas.guest_pass import (
    GuestBookingGrantCreate,
    GuestBookingGrantResponse,
    GuestFunnelResponse,
    GuestLinkEventCreate,
)
from services.sessions_service.services.guest_booking import (
    GUEST_PASS_RESERVATION_MINUTES,
    lifecycle_mode,
    member_guest_url,
    receipt_url,
)

router = APIRouter(tags=["guest-booking-operations"])


@router.get("/sessions/{session_id}/guest-share-link")
async def guest_share_link(
    session_id: uuid.UUID,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, str | None]:
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    # The member-facing access check prevents discovery of a private cohort,
    # Pod or Event via a guessed ID.
    from services.sessions_service.routers.member import _decorate_session_for_user

    decorated = await _decorate_session_for_user(session, user, db)
    if isinstance(decorated, dict) and not decorated.get("access", {}).get(
        "visible", True
    ):
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "url": await member_guest_url(session, user.user_id, db),
        "booking_mode": lifecycle_mode(session),
    }


@router.post(
    "/admin/sessions/{session_id}/guest-booking-links",
    response_model=GuestBookingGrantResponse,
)
async def issue_guest_link(
    session_id: uuid.UUID,
    body: GuestBookingGrantCreate,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    session = await db.get(Session, session_id)
    if not session or session.status in {"draft", "cancelled"}:
        raise HTTPException(status_code=404, detail="Session not found")
    if (
        not session.allows_guests
        or session.guest_booking_mode == "disabled"
        or session.guest_fee_kobo is None
    ):
        raise HTTPException(
            status_code=422,
            detail="Enable guest self-booking and set an explicit guest price first",
        )
    token = secrets.token_urlsafe(32)
    grant = GuestBookingGrant(
        session_id=session_id,
        email=str(body.email).lower(),
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        created_by=admin.user_id,
        expires_at=utc_now() + timedelta(hours=body.expires_in_hours),
    )
    db.add(grant)
    await db.commit()
    await db.refresh(grant)
    url = f"{get_settings().FRONTEND_URL.rstrip('/')}/guest-pass/session/{session_id}?{urlencode({'source': 'admin_share'})}#token={token}"
    return GuestBookingGrantResponse(id=grant.id, url=url, expires_at=grant.expires_at)


@router.get("/admin/sessions/{session_id}/guest-share-link")
async def admin_guest_share_link(
    session_id: uuid.UUID,
    referrer_auth_id: str,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, str | None]:
    session = await db.get(Session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"url": await member_guest_url(session, referrer_auth_id, db)}


@router.delete("/admin/guest-booking-links/{grant_id}", status_code=204)
async def revoke_guest_link(
    grant_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    grant = (
        await db.execute(
            select(GuestBookingGrant)
            .where(GuestBookingGrant.id == grant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not grant:
        raise HTTPException(status_code=404, detail="Guest link not found")
    grant.revoked_at = utc_now()
    await db.commit()


@router.post("/admin/guest-passes/{guest_pass_id}/payment-link")
async def restore_guest_payment_link(
    guest_pass_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, str]:
    """Restore the same pass/reference without creating duplicate guest identities."""
    from services.sessions_service.routers.guest_passes import _spaces_remaining

    session_id = (
        await db.execute(
            select(GuestPass.session_id).where(GuestPass.id == guest_pass_id)
        )
    ).scalar_one_or_none()
    if not session_id:
        raise HTTPException(status_code=404, detail="Guest pass not found")
    # Consistent session → pass lock order with creation and payment callbacks.
    session = (
        await db.execute(
            select(Session).where(Session.id == session_id).with_for_update()
        )
    ).scalar_one()
    guest = (
        await db.execute(
            select(GuestPass).where(GuestPass.id == guest_pass_id).with_for_update()
        )
    ).scalar_one()
    if session.status not in {"scheduled", "in_progress", "completed"}:
        raise HTTPException(
            status_code=409, detail="This session is no longer available"
        )
    if guest.status in {"confirmed", "attended"}:
        await db.commit()
        return {"url": receipt_url(guest.id)}
    if guest.status not in {"pending_payment", "payment_failed"}:
        raise HTTPException(
            status_code=409, detail="This guest booking cannot be restored"
        )
    if session.starts_at <= utc_now():
        guest.booking_mode = "settlement"
        guest.reservation_expires_at = None
    else:
        if await _spaces_remaining(session, db, exclude_guest_pass_id=guest.id) <= 0:
            raise HTTPException(
                status_code=409, detail="This session has no spaces available"
            )
        guest.booking_mode = "reservation"
        guest.reservation_expires_at = utc_now() + timedelta(
            minutes=GUEST_PASS_RESERVATION_MINUTES
        )
    await db.commit()
    return {"url": receipt_url(guest.id)}


@router.post("/sessions/{session_id}/guest-link-events", status_code=204)
async def record_guest_link_event(
    session_id: uuid.UUID,
    body: GuestLinkEventCreate,
    db: AsyncSession = Depends(get_async_db),
) -> None:
    # No emails, phones, IPs, referrer identities or approval tokens are stored.
    session = await db.get(Session, session_id)
    if (
        not session
        or session.status in {"draft", "cancelled"}
        or session.guest_booking_mode == "disabled"
    ):
        raise HTTPException(status_code=404, detail="Session not found")
    await db.execute(
        insert(GuestLinkEvent)
        .values(session_id=session_id, **body.model_dump())
        .on_conflict_do_nothing(index_elements=[GuestLinkEvent.id])
    )
    await db.commit()


@router.get("/admin/guest-passes/funnel", response_model=GuestFunnelResponse)
async def guest_funnel(
    session_id: uuid.UUID | None = None,
    booking_source: str | None = None,
    campaign_key: str | None = None,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    passes = select(
        func.count(GuestPass.id),
        func.count(GuestPass.id).filter(
            GuestPass.status.in_(["confirmed", "attended"])
        ),
        func.count(GuestPass.id).filter(GuestPass.attended_at.is_not(None)),
        func.count(GuestPass.id).filter(GuestPass.assessment_result.is_not(None)),
        func.count(GuestPass.id).filter(GuestPass.converted_member_id.is_not(None)),
    )
    links = select(GuestLinkEvent.event_type, func.count(GuestLinkEvent.id)).group_by(
        GuestLinkEvent.event_type
    )
    for field, value in [
        ("session_id", session_id),
        ("booking_source", booking_source),
        ("campaign_key", campaign_key),
    ]:
        if value:
            passes = passes.where(getattr(GuestPass, field) == value)
            links = links.where(getattr(GuestLinkEvent, field) == value)
    counts = (await db.execute(passes)).one()
    events = dict((await db.execute(links)).all())
    return GuestFunnelResponse(
        link_views=events.get("view", 0),
        link_shares=events.get("share", 0),
        checkout_started=counts[0],
        paid=counts[1],
        attended=counts[2],
        assessed=counts[3],
        converted=counts[4],
    )


@router.get(
    "/admin/sessions/guest-booking-options", response_model=list[SessionResponse]
)
async def guest_booking_options(
    offset: int = 0,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """All session types, including history for individual reconciliation."""
    if offset < 0:
        raise HTTPException(status_code=422, detail="Offset must be positive")
    return list(
        (
            await db.execute(
                select(Session)
                .where(
                    Session.allows_guests.is_(True),
                    Session.guest_booking_mode != "disabled",
                    Session.guest_fee_kobo.is_not(None),
                    Session.status.in_(["scheduled", "in_progress", "completed"]),
                )
                .order_by(Session.starts_at.desc(), Session.id)
                .offset(offset)
                .limit(100)
            )
        ).scalars()
    )
