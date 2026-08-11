"""Public self-paying guest passes and protected operations follow-up."""

import uuid
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin, require_service_role
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.service_client import internal_get, internal_post
from libs.db.session import get_async_db
from services.sessions_service.models import (
    GuestPass,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionStatus,
)
from services.sessions_service.schemas import (
    GuestPassAdminResponse,
    GuestPassAttendanceUpdate,
    GuestPassConfirm,
    GuestPassCreate,
    GuestPassOffer,
    GuestPassPublicResponse,
    GuestReferralRewardPaid,
)

router = APIRouter(tags=["guest-passes"])
settings = get_settings()


def _normalize_guest_phone(phone: str) -> str:
    """Store a stable Nigerian/E.164-like value for repeat-guest deduplication."""
    stripped = phone.strip()
    digits = re.sub(r"\D", "", stripped)
    if stripped.startswith("+"):
        return f"+{digits}"
    if digits.startswith("234"):
        return f"+{digits}"
    if len(digits) == 11 and digits.startswith("0"):
        return f"+234{digits[1:]}"
    return digits


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


async def _spaces_remaining(session: Session, db: AsyncSession) -> int:
    booked = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(SessionBooking.party_size), 0)).where(
                    SessionBooking.session_id == session.id,
                    SessionBooking.status.in_(
                        [SessionBookingStatus.PENDING, SessionBookingStatus.CONFIRMED]
                    ),
                )
            )
        ).scalar_one()
        or 0
    )
    guest_count = int(
        (
            await db.execute(
                select(func.count(GuestPass.id)).where(
                    GuestPass.session_id == session.id,
                    GuestPass.status.in_(["pending_payment", "confirmed", "attended"]),
                )
            )
        ).scalar_one()
        or 0
    )
    return max(0, session.capacity - booked - guest_count)


@router.get("/sessions/{session_id}/guest-pass", response_model=GuestPassOffer)
async def guest_pass_offer(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    session = await db.get(Session, session_id)
    if session is None or session.status != SessionStatus.SCHEDULED:
        raise HTTPException(status_code=404, detail="Session not found")
    guest_fee = (
        session.guest_fee_kobo
        if session.guest_fee_kobo is not None
        else session.pool_fee
    )
    return GuestPassOffer(
        session_id=session.id,
        title=session.title,
        location_name=session.location_name,
        starts_at=session.starts_at,
        ends_at=session.ends_at,
        guest_fee_kobo=guest_fee,
        community_dropin_fee_kobo=session.community_dropin_fee_kobo,
        allows_guests=session.allows_guests,
        spaces_remaining=await _spaces_remaining(session, db),
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
    session = await db.get(Session, session_id)
    if session is None or session.status != SessionStatus.SCHEDULED:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.allows_guests or await _spaces_remaining(session, db) < 1:
        raise HTTPException(
            status_code=409,
            detail="Guest passes are not available for this session",
        )
    price_kobo = (
        session.guest_fee_kobo
        if session.guest_fee_kobo is not None
        else session.pool_fee
    )
    referral_code = body.referral_code.upper().strip() if body.referral_code else None
    referrer_auth_id = (
        await _resolve_referrer_auth_id(referral_code) if referral_code else None
    )
    guest_pass = GuestPass(
        session_id=session.id,
        full_name=body.full_name.strip(),
        email=str(body.email).lower(),
        phone=_normalize_guest_phone(body.phone),
        date_of_birth=body.date_of_birth,
        guardian_name=body.guardian_name,
        guardian_phone=body.guardian_phone,
        waiver_accepted_at=utc_now(),
        marketing_consent=body.marketing_consent,
        referral_code=referral_code,
        referrer_auth_id=referrer_auth_id,
        price_kobo=price_kobo,
        total_kobo=price_kobo,
        referral_reward_kobo=session.guest_referral_reward_kobo,
        payment_reference=f"GUEST-{uuid.uuid4().hex[:20].upper()}",
    )
    db.add(guest_pass)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A guest pass already exists for this phone and session",
        ) from exc
    await db.refresh(guest_pass)

    response = await internal_post(
        service_url=settings.PAYMENTS_SERVICE_URL,
        path="/internal/payments/initialize",
        calling_service="sessions",
        json={
            "purpose": "guest_pass",
            "amount": price_kobo / 100,
            "currency": "NGN",
            "reference": guest_pass.payment_reference,
            "member_auth_id": f"guest:{guest_pass.id}",
            "callback_url": f"/guest-pass/{guest_pass.id}",
            "metadata": {
                "guest_pass_id": str(guest_pass.id),
                "session_id": str(session.id),
                "payer_email": guest_pass.email,
                "referral_code": guest_pass.referral_code,
            },
        },
        timeout=30,
    )
    if response.status_code >= 400:
        guest_pass.status = "payment_failed"
        await db.commit()
        raise HTTPException(status_code=502, detail="Could not start guest payment")
    checkout = response.json()
    guest_pass.additional_charges = checkout.get("additional_charges") or []
    guest_pass.total_kobo = int(checkout.get("amount_kobo") or price_kobo)
    await db.commit()
    await db.refresh(guest_pass)
    result = GuestPassPublicResponse.model_validate(guest_pass)
    return result.model_copy(update={"checkout_url": checkout.get("authorization_url")})


@router.get("/guest-passes/{guest_pass_id}", response_model=GuestPassPublicResponse)
async def get_guest_pass_status(
    guest_pass_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Redacted public receipt; identity and assessment data are never returned."""
    guest_pass = await db.get(GuestPass, guest_pass_id)
    if guest_pass is None:
        raise HTTPException(status_code=404, detail="Guest pass not found")
    return guest_pass


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
    guest_pass = await db.get(GuestPass, guest_pass_id)
    if guest_pass is None or guest_pass.payment_reference != body.payment_reference:
        raise HTTPException(status_code=404, detail="Guest pass not found")
    if guest_pass.status != "attended":
        guest_pass.status = "confirmed"
    await db.commit()
    await db.refresh(guest_pass)
    await get_email_client().send(
        to_email=guest_pass.email,
        subject="Your SwimBuddz guest pass is confirmed",
        body=(
            f"Hi {guest_pass.full_name},\n\nYour SwimBuddz guest pass is confirmed. "
            f"Your reference is {guest_pass.payment_reference}. We look forward to swimming with you."
        ),
    )
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
    guest_pass = await db.get(GuestPass, guest_pass_id)
    if guest_pass is None:
        raise HTTPException(status_code=404, detail="Guest pass not found")
    if guest_pass.status not in {"confirmed", "attended"}:
        raise HTTPException(
            status_code=409, detail="Only a paid guest pass can be attended"
        )
    first_attended = (
        await db.execute(
            select(GuestPass.id).where(
                GuestPass.phone == guest_pass.phone,
                GuestPass.id != guest_pass.id,
                GuestPass.attended_at.is_not(None),
            )
        )
    ).first() is None
    guest_pass.status = "attended"
    guest_pass.attended_at = guest_pass.attended_at or utc_now()
    guest_pass.actual_swim_minutes = body.actual_swim_minutes
    guest_pass.assessment_result = body.assessment_result
    if guest_pass.referrer_auth_id and first_attended:
        guest_pass.referral_reward_status = "eligible"
    await db.commit()
    await db.refresh(guest_pass)
    if body.assessment_result and body.send_assessment_email:
        summary = "\n".join(
            f"{key.replace('_', ' ').title()}: {value}"
            for key, value in body.assessment_result.items()
        )
        await get_email_client().send(
            to_email=guest_pass.email,
            subject="Your SwimBuddz swim assessment",
            body=(
                f"Hi {guest_pass.full_name},\n\nHere is the assessment from your swim:\n\n"
                f"{summary}\n\nKeep swimming,\nSwimBuddz"
            ),
        )
    return guest_pass


@router.post(
    "/admin/guest-passes/{guest_pass_id}/referral-reward/paid",
    response_model=GuestPassAdminResponse,
)
async def mark_guest_referral_reward_paid(
    guest_pass_id: uuid.UUID,
    body: GuestReferralRewardPaid,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    guest_pass = await db.get(GuestPass, guest_pass_id)
    if guest_pass is None:
        raise HTTPException(status_code=404, detail="Guest pass not found")
    if guest_pass.referral_reward_status not in {"eligible", "paid"}:
        raise HTTPException(
            status_code=409, detail="This referral reward is not eligible"
        )
    guest_pass.referral_reward_status = "paid"
    guest_pass.referral_reward_reference = body.transfer_reference
    await db.commit()
    await db.refresh(guest_pass)
    return guest_pass
