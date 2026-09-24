"""Public self-paying guest passes and protected operations follow-up."""

import uuid
from datetime import timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin, require_service_role
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.service_client import emit_rewards_event, internal_get
from libs.db.session import get_async_db
from services.sessions_service.models import (
    GuestPass,
    GuestReferralClaim,
    Session,
    SessionBooking,
    SessionBookingStatus,
)
from services.sessions_service.schemas import (
    GuestPassAdminResponse,
    GuestPassAttendanceUpdate,
    GuestPassConfirm,
    GuestPassCreate,
    GuestPassOffer,
    GuestPassPublicResponse,
)
from services.sessions_service.services.guest_identity import (
    normalize_guest_phone as _normalize_guest_phone,
)

router = APIRouter(tags=["guest-passes"])
settings = get_settings()
from services.sessions_service.services.booking_confirmation import (
    deliver_confirmation,
    queue_confirmation,
)
from services.sessions_service.services.guest_booking import (
    GUEST_PASS_RESERVATION_MINUTES,
    SAFETY_ACKNOWLEDGEMENT_VERSION,
    event_guest_context,
    has_receipt_access,
    lifecycle_mode,
    public_location,
    public_receipt,
    require_admission,
    resolve_grant,
    resolve_member_invitation,
)
from services.sessions_service.services.guest_checkout import start_checkout


async def _resolve_referrer_auth_id(referral_code: str) -> str:
    try:
        response = await internal_get(
            service_url=settings.WALLET_SERVICE_URL,
            path="/internal/wallet/referral-code/resolve",
            calling_service="sessions",
            params={"code": referral_code},
            timeout=10,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail="Referral attribution is temporarily unavailable",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=400, detail="Referral code is not valid")
    referrer_auth_id = response.json().get("referrer_auth_id")
    if not referrer_auth_id:
        raise HTTPException(status_code=502, detail="Referral attribution failed")
    return str(referrer_auth_id)


async def _spaces_remaining(
    session: Session,
    db: AsyncSession,
    *,
    exclude_guest_pass_id: uuid.UUID | None = None,
) -> int:
    now = utc_now()
    booked = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(SessionBooking.party_size), 0)).where(
                    SessionBooking.session_id == session.id,
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
        or 0
    )
    guest_conditions = [
        GuestPass.session_id == session.id,
        GuestPass.booking_mode == "reservation",
        or_(
            GuestPass.status.in_(["confirmed", "attended"]),
            and_(
                GuestPass.status.in_(["pending_payment", "payment_failed"]),
                GuestPass.reservation_expires_at.is_not(None),
                GuestPass.reservation_expires_at > now,
            ),
        ),
    ]
    if exclude_guest_pass_id is not None:
        guest_conditions.append(GuestPass.id != exclude_guest_pass_id)
    guest_count = int(
        (
            await db.execute(select(func.count(GuestPass.id)).where(*guest_conditions))
        ).scalar_one()
        or 0
    )
    return max(0, session.capacity - booked - guest_count)


@router.get("/sessions/{session_id}/guest-pass", response_model=GuestPassOffer)
async def guest_pass_offer(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    x_guest_booking_token: str | None = Header(default=None),
    x_guest_invite_token: Annotated[str | None, Header()] = None,
):
    session = await db.get(Session, session_id)
    if session is None or session.status in {"draft", "cancelled"}:
        raise HTTPException(status_code=404, detail="Session not found")
    grant = await resolve_grant(db, session_id, x_guest_booking_token)
    invitation = await resolve_member_invitation(db, session_id, x_guest_invite_token)
    event = await event_guest_context(session, has_grant=grant is not None)
    mode = lifecycle_mode(session, has_grant=grant is not None)
    location, _address = public_location(session, event)
    return GuestPassOffer(
        session_id=session.id,
        title=session.title,
        location_name=location,
        starts_at=session.starts_at,
        ends_at=session.ends_at,
        timezone=session.timezone,
        guest_fee_kobo=session.guest_fee_kobo,
        community_dropin_fee_kobo=session.community_dropin_fee_kobo,
        allows_guests=session.allows_guests,
        spaces_remaining=await _spaces_remaining(session, db)
        if mode == "reservation"
        else None,
        booking_mode=mode,
        guest_booking_mode=session.guest_booking_mode,
        booking_closes_at=session.guest_booking_closes_at or session.starts_at,
        reconciliation_closes_at=session.ends_at
        + timedelta(days=session.guest_reconciliation_days),
        approval_granted=grant is not None,
        member_invitation_valid=invitation is not None,
    )


@router.post(
    "/sessions/{session_id}/guest-passes",
    response_model=GuestPassPublicResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_guest_pass(
    session_id: uuid.UUID,
    body: GuestPassCreate,
    db: AsyncSession = Depends(get_async_db),
):
    initial_session = await db.get(Session, session_id)
    if initial_session is None or initial_session.status in {"draft", "cancelled"}:
        raise HTTPException(status_code=404, detail="Session not found")
    referral_code = body.referral_code.upper().strip() if body.referral_code else None
    referrer_auth_id = (
        await _resolve_referrer_auth_id(referral_code) if referral_code else None
    )
    # HTTP policy/referral reads happen before the capacity lock.
    grant = await resolve_grant(db, session_id, body.access_token)
    await event_guest_context(initial_session, has_grant=grant is not None)
    await db.rollback()
    session = (
        await db.execute(
            select(Session).where(Session.id == session_id).with_for_update()
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    grant = await resolve_grant(db, session_id, body.access_token, lock=True)
    invitation = await resolve_member_invitation(
        db, session_id, body.invite_token, lock=True
    )
    mode = require_admission(
        session, member_invitation=invitation, grant=grant, email=str(body.email)
    )
    if mode == "reservation" and await _spaces_remaining(session, db) < 1:
        raise HTTPException(
            status_code=409, detail="This swim has no guest spaces remaining"
        )
    now = utc_now()
    guest_pass = GuestPass(
        id=uuid.uuid4(),
        session_id=session.id,
        full_name=body.full_name,
        email=str(body.email).lower(),
        phone=_normalize_guest_phone(body.phone),
        date_of_birth=body.date_of_birth,
        guardian_name=body.guardian_name,
        guardian_phone=body.guardian_phone,
        waiver_accepted_at=now,
        safety_acknowledgement_version=SAFETY_ACKNOWLEDGEMENT_VERSION,
        marketing_consent=body.marketing_consent,
        referral_code=referral_code,
        referrer_auth_id=referrer_auth_id,
        price_kobo=session.guest_fee_kobo,
        total_kobo=session.guest_fee_kobo,
        booking_mode=mode,
        booking_source=body.booking_source
        or ("member_share" if referral_code else "direct"),
        campaign_key=body.campaign_key,
        referral_reward_bubbles=10,
        reservation_expires_at=now + timedelta(minutes=GUEST_PASS_RESERVATION_MINUTES)
        if mode == "reservation"
        else None,
        payment_method=body.payment_method,
        payment_reference=f"GUEST-{uuid.uuid4().hex[:20].upper()}",
    )
    db.add(guest_pass)
    if grant:
        grant.used_by_pass_id = guest_pass.id
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A guest booking already exists for this phone and swim. Use your private receipt link or contact SwimBuddz.",
        ) from exc
    return await start_checkout(db, guest_pass.id)


@router.get("/guest-passes/{guest_pass_id}", response_model=GuestPassPublicResponse)
async def get_guest_pass_status(
    guest_pass_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    x_guest_pass_token: str | None = Header(default=None),
):
    """Redacted receipt; a private capability additionally unlocks venue details."""
    guest_pass = await db.get(GuestPass, guest_pass_id)
    if guest_pass is None:
        raise HTTPException(status_code=404, detail="Guest pass not found")
    session = await db.get(Session, guest_pass.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return await public_receipt(
        guest_pass,
        session,
        private=has_receipt_access(guest_pass_id, x_guest_pass_token),
    )


@router.post(
    "/guest-passes/{guest_pass_id}/checkout", response_model=GuestPassPublicResponse
)
async def retry_guest_checkout(
    guest_pass_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    x_guest_pass_token: str | None = Header(default=None),
):
    if not has_receipt_access(guest_pass_id, x_guest_pass_token):
        raise HTTPException(status_code=404, detail="Guest pass not found")
    if await db.get(GuestPass, guest_pass_id) is None:
        raise HTTPException(status_code=404, detail="Guest pass not found")
    return await start_checkout(db, guest_pass_id)


@router.post(
    "/internal/sessions/guest-passes/{guest_pass_id}/confirm",
    response_model=GuestPassPublicResponse,
)
async def confirm_guest_pass(
    guest_pass_id: uuid.UUID,
    body: GuestPassConfirm,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    # Always session -> pass, matching reservation lock order.
    session_id = (
        await db.execute(
            select(GuestPass.session_id).where(GuestPass.id == guest_pass_id)
        )
    ).scalar_one_or_none()
    if session_id is None:
        raise HTTPException(status_code=404, detail="Guest pass not found")
    session = (
        await db.execute(
            select(Session).where(Session.id == session_id).with_for_update()
        )
    ).scalar_one_or_none()
    guest_pass = (
        await db.execute(
            select(GuestPass).where(GuestPass.id == guest_pass_id).with_for_update()
        )
    ).scalar_one()
    if guest_pass.payment_reference != body.payment_reference:
        raise HTTPException(status_code=404, detail="Guest pass not found")
    if guest_pass.status not in {
        "pending_payment",
        "payment_failed",
        "confirmed",
        "attended",
    }:
        raise HTTPException(
            status_code=409, detail="This guest payment requires manual review"
        )
    if guest_pass.status not in {"confirmed", "attended"}:
        if session is None or session.status in {"cancelled", "draft"}:
            raise HTTPException(
                status_code=409,
                detail="The guest session is unavailable; payment requires review",
            )
        if session.starts_at <= utc_now():
            # Delayed bank approval / callback is settlement of an existing
            # booking. It never asserts that the payer attended.
            guest_pass.booking_mode = "settlement"
        if (
            guest_pass.booking_mode == "reservation"
            and (
                not guest_pass.reservation_expires_at
                or guest_pass.reservation_expires_at <= utc_now()
            )
            and await _spaces_remaining(
                session, db, exclude_guest_pass_id=guest_pass.id
            )
            < 1
        ):
            raise HTTPException(
                status_code=409,
                detail="The guest reservation expired and the swim is now full; payment requires review",
            )
        guest_pass.status = "confirmed"
        guest_pass.reservation_expires_at = None
    key = await queue_confirmation(db, guest_pass.id, guest=True)
    await db.commit()
    await deliver_confirmation(db, key)
    return guest_pass


@router.get("/admin/guest-passes", response_model=list[GuestPassAdminResponse])
async def list_guest_passes(
    session_id: uuid.UUID | None = None,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(GuestPass)
    if session_id:
        query = query.where(GuestPass.session_id == session_id)
    return list(
        (await db.execute(query.order_by(GuestPass.created_at.desc()))).scalars()
    )


@router.post(
    "/admin/guest-passes/{guest_pass_id}/attendance",
    response_model=GuestPassAdminResponse,
)
async def mark_guest_pass_attended(
    guest_pass_id: uuid.UUID,
    body: GuestPassAttendanceUpdate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    guest_pass = (
        await db.execute(
            select(GuestPass).where(GuestPass.id == guest_pass_id).with_for_update()
        )
    ).scalar_one_or_none()
    if guest_pass is None:
        raise HTTPException(status_code=404, detail="Guest pass not found")
    if guest_pass.status not in {"confirmed", "attended"}:
        raise HTTPException(
            status_code=409, detail="Only a paid guest pass can be attended"
        )
    guest_pass.status = "attended"
    guest_pass.attended_at = guest_pass.attended_at or utc_now()
    guest_pass.actual_swim_minutes = body.actual_swim_minutes
    guest_pass.assessment_result = body.assessment_result
    claim_status = "pending" if guest_pass.referrer_auth_id else "not_eligible"
    claim_id = (
        await db.execute(
            insert(GuestReferralClaim)
            .values(
                guest_phone=guest_pass.phone,
                guest_pass_id=guest_pass.id,
                referrer_auth_id=guest_pass.referrer_auth_id,
                status=claim_status,
            )
            .on_conflict_do_nothing(index_elements=[GuestReferralClaim.guest_phone])
            .returning(GuestReferralClaim.id)
        )
    ).scalar_one_or_none()
    claim = (
        await db.get(GuestReferralClaim, claim_id)
        if claim_id is not None
        else (
            await db.execute(
                select(GuestReferralClaim).where(
                    GuestReferralClaim.guest_phone == guest_pass.phone
                )
            )
        ).scalar_one()
    )
    should_reward_referrer = bool(
        guest_pass.referrer_auth_id
        and claim.guest_pass_id == guest_pass.id
        and claim.status == "pending"
        and guest_pass.referral_reward_status != "granted"
    )
    if should_reward_referrer:
        guest_pass.referral_reward_status = "pending"
    await db.commit()
    await db.refresh(guest_pass)

    if should_reward_referrer and guest_pass.referrer_auth_id:
        reward_result = await emit_rewards_event(
            event_type="referral.guest_attended",
            member_auth_id=guest_pass.referrer_auth_id,
            service_source="sessions",
            event_data={
                "guest_name": guest_pass.full_name,
                "guest_pass_id": str(guest_pass.id),
                "session_id": str(guest_pass.session_id),
            },
            idempotency_key=f"guest-referral-attended-{guest_pass.id}",
            calling_service="sessions",
            occurred_at=guest_pass.attended_at.isoformat(),
        )
        if reward_result is not None:
            granted = int(reward_result.get("rewards_granted") or 0) or sum(
                int(reward.get("bubbles", 0))
                for reward in reward_result.get("rewards", [])
            )
            if granted:
                guest_pass.referral_reward_bubbles = granted
                guest_pass.referral_reward_status = "granted"
                claim.status = "granted"
                claim.rewarded_at = utc_now()
            await db.commit()
            await db.refresh(guest_pass)
    if body.assessment_result and body.send_assessment_email:
        await get_email_client().send_template(
            template_type="guest_pass_assessment",
            to_email=guest_pass.email,
            template_data={
                "guest_name": guest_pass.full_name,
                "assessment": body.assessment_result,
            },
        )
    return guest_pass
