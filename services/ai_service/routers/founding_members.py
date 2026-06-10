"""Stroke Lab founding-members pre-sale.

Money flows through payments_service like every other purpose — ai_service
never touches Paystack directly. Two entry points create the founding-member
row, both idempotent on member_auth_id:

  * Webhook path (source of truth): Paystack → payments_service marks the
    Payment paid → entitlement dispatcher calls
    POST /internal/ai/founding-members/confirm here.
  * Client fallback: after Paystack redirects the user back, the frontend
    calls POST /ai/founding-members/claim, which verifies the reference via
    payments_service and records. Essential in local/dev where Paystack
    can't reach a localhost webhook.

Public + member routes:
  GET  /ai/founding-members/stats        public — live seat counter
  GET  /ai/founding-members/me           authed — has the caller claimed?
  POST /ai/founding-members/initialize   authed — start a Paystack checkout
  POST /ai/founding-members/claim        authed — verify + record (fallback)

Internal route (service-role only):
  POST /internal/ai/founding-members/confirm  payments_service → record
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user, require_service_role
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import internal_get, internal_post
from libs.db.session import get_async_db
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai_service.models import (
    FOUNDING_MEMBER_PRICE_KOBO,
    FOUNDING_MEMBERS_CAP,
    StrokeLabFoundingMember,
)

logger = get_logger(__name__)

# Mounted at /ai → /ai/founding-members
router = APIRouter(prefix="/founding-members", tags=["stroke-lab-founding"])

# Mounted at root → /internal/ai/founding-members
internal_router = APIRouter(
    prefix="/internal/ai/founding-members", tags=["stroke-lab-founding-internal"]
)

_PRICE_NGN = FOUNDING_MEMBER_PRICE_KOBO // 100


# ── Schemas ──────────────────────────────────────────────────────


class FoundingStatsResponse(BaseModel):
    seats_total: int = Field(FOUNDING_MEMBERS_CAP)
    seats_taken: int
    seats_remaining: int
    price_kobo: int = Field(FOUNDING_MEMBER_PRICE_KOBO)
    price_ngn: int = Field(_PRICE_NGN)
    is_sold_out: bool


class FoundingMemberStatus(BaseModel):
    is_founding_member: bool
    claimed_at: Optional[str] = None
    paystack_reference: Optional[str] = None


class FoundingInitializeResponse(BaseModel):
    authorization_url: str
    reference: str


class FoundingClaimRequest(BaseModel):
    paystack_reference: str = Field(min_length=4, max_length=64)


class FoundingClaimResponse(BaseModel):
    seat_number: int
    paystack_reference: str
    amount_paid_kobo: int


class FoundingConfirmRequest(BaseModel):
    member_auth_id: str
    payment_reference: str
    amount_kobo: int


class FoundingConfirmResponse(BaseModel):
    recorded: bool
    seat_number: int


# ── Helpers ──────────────────────────────────────────────────────


async def _seats_taken(db: AsyncSession) -> int:
    return int(
        (await db.execute(select(func.count(StrokeLabFoundingMember.id)))).scalar_one()
    )


async def _existing_for_caller(
    db: AsyncSession, member_auth_id: uuid.UUID
) -> Optional[StrokeLabFoundingMember]:
    stmt = select(StrokeLabFoundingMember).where(
        StrokeLabFoundingMember.member_auth_id == member_auth_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _record_founding_member(
    db: AsyncSession,
    member_auth_id: uuid.UUID,
    reference: str,
    amount_kobo: int,
) -> tuple[StrokeLabFoundingMember, int]:
    """Idempotently record a founding member. Returns (row, seat_number).

    Lenient on the cap on purpose: this is only ever called AFTER a
    payment has cleared (webhook confirm or client claim). Refusing a
    paid customer because we hit 100 would strand their money; we'd
    rather oversell by one or two and refund/handle manually. The strict
    cap gate lives in /initialize, before any money moves.
    """
    existing = await _existing_for_caller(db, member_auth_id)
    if existing is not None:
        # Determine a stable 1-indexed seat number by creation order.
        seat = await _seat_number_of(db, existing)
        return existing, seat

    taken = await _seats_taken(db)
    if taken >= FOUNDING_MEMBERS_CAP:
        logger.warning(
            "Stroke Lab founding cap reached (%d) but recording paid member "
            "%s anyway (ref=%s) — oversell, handle manually.",
            taken,
            member_auth_id,
            reference,
        )

    row = StrokeLabFoundingMember(
        member_auth_id=member_auth_id,
        paystack_reference=reference,
        amount_paid_kobo=amount_kobo,
        paid_at=utc_now(),
    )
    db.add(row)
    try:
        await db.commit()
    except Exception as exc:
        # Unique constraint race (member_auth_id or reference) — recover
        # by returning the now-existing row.
        await db.rollback()
        logger.warning(
            "Stroke Lab founding insert race for %s: %s", member_auth_id, exc
        )
        existing = await _existing_for_caller(db, member_auth_id)
        if existing is not None:
            seat = await _seat_number_of(db, existing)
            return existing, seat
        raise
    await db.refresh(row)
    seat = await _seat_number_of(db, row)
    return row, seat


async def _seat_number_of(db: AsyncSession, row: StrokeLabFoundingMember) -> int:
    """1-indexed position by created_at (informational for UX)."""
    count_before = int(
        (
            await db.execute(
                select(func.count(StrokeLabFoundingMember.id)).where(
                    StrokeLabFoundingMember.created_at <= row.created_at
                )
            )
        ).scalar_one()
    )
    return max(1, count_before)


def _caller_uuid(current_user: AuthUser) -> uuid.UUID:
    try:
        return uuid.UUID(current_user.user_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid auth user id") from exc


# ── Public + member routes ───────────────────────────────────────


@router.get("/stats", response_model=FoundingStatsResponse, summary="Public counter")
async def founding_stats(
    db: AsyncSession = Depends(get_async_db),
) -> FoundingStatsResponse:
    taken = await _seats_taken(db)
    remaining = max(0, FOUNDING_MEMBERS_CAP - taken)
    return FoundingStatsResponse(
        seats_taken=taken,
        seats_remaining=remaining,
        is_sold_out=remaining == 0,
    )


@router.get(
    "/me",
    response_model=FoundingMemberStatus,
    summary="Has the authenticated caller already claimed?",
)
async def my_founding_status(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> FoundingMemberStatus:
    member_auth_id = _caller_uuid(current_user)
    existing = await _existing_for_caller(db, member_auth_id)
    if existing is None:
        return FoundingMemberStatus(is_founding_member=False)
    return FoundingMemberStatus(
        is_founding_member=True,
        claimed_at=existing.created_at.isoformat() if existing.created_at else None,
        paystack_reference=existing.paystack_reference,
    )


@router.post(
    "/initialize",
    response_model=FoundingInitializeResponse,
    summary="Start a Paystack checkout for a founding-member spot",
)
async def initialize_founding_payment(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> FoundingInitializeResponse:
    member_auth_id = _caller_uuid(current_user)

    if await _existing_for_caller(db, member_auth_id) is not None:
        raise HTTPException(status_code=409, detail="You're already a founding member.")

    # Strict cap gate — block new checkouts once full.
    if await _seats_taken(db) >= FOUNDING_MEMBERS_CAP:
        raise HTTPException(status_code=409, detail="All founding spots are sold out")

    settings = get_settings()
    reference = f"strokelab-{uuid.uuid4().hex}"

    try:
        resp = await internal_post(
            service_url=settings.PAYMENTS_SERVICE_URL,
            path="/internal/payments/initialize",
            calling_service="ai",
            json={
                "purpose": "strokelab_founding",
                "amount": _PRICE_NGN,  # Naira; payments converts to kobo
                "currency": "NGN",
                "reference": reference,
                "member_auth_id": str(member_auth_id),
                # Paystack appends ?reference=&trxref= on redirect back.
                "callback_url": "/founding-members",
                "metadata": {
                    "product": "stroke-lab-founding",
                    "payer_email": current_user.email,
                },
            },
        )
    except Exception as exc:
        logger.exception("Stroke Lab payment init call failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not start payment") from exc

    if resp.status_code >= 400:
        logger.error(
            "payments initialize returned %s: %s", resp.status_code, resp.text[:200]
        )
        raise HTTPException(status_code=502, detail="Could not start payment")

    data = resp.json()
    auth_url = data.get("authorization_url")
    if not auth_url:
        raise HTTPException(
            status_code=502, detail="Payment provider gave no checkout URL"
        )
    return FoundingInitializeResponse(
        authorization_url=auth_url, reference=data.get("reference", reference)
    )


@router.post(
    "/claim",
    response_model=FoundingClaimResponse,
    summary="Verify a reference via payments_service and record (client fallback)",
)
async def claim_founding_member(
    body: FoundingClaimRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> FoundingClaimResponse:
    member_auth_id = _caller_uuid(current_user)

    existing = await _existing_for_caller(db, member_auth_id)
    if existing is not None:
        seat = await _seat_number_of(db, existing)
        return FoundingClaimResponse(
            seat_number=seat,
            paystack_reference=existing.paystack_reference,
            amount_paid_kobo=existing.amount_paid_kobo,
        )

    settings = get_settings()
    try:
        resp = await internal_get(
            service_url=settings.PAYMENTS_SERVICE_URL,
            path=f"/internal/payments/paystack/verify/{body.paystack_reference}",
            calling_service="ai",
        )
    except Exception as exc:
        logger.exception("Stroke Lab verify call failed: %s", exc)
        raise HTTPException(status_code=502, detail="Could not verify payment") from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail="Could not verify that payment")

    data = resp.json()
    if (data.get("status") or "") != "completed":
        raise HTTPException(status_code=400, detail="Payment is not completed")
    amount_kobo = int(data.get("amount_kobo") or 0)
    currency = str(data.get("currency") or "")
    if amount_kobo < FOUNDING_MEMBER_PRICE_KOBO:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Payment of ₦{amount_kobo // 100} is below the founding price "
                f"of ₦{_PRICE_NGN}."
            ),
        )
    if currency and currency.upper() != "NGN":
        raise HTTPException(
            status_code=400, detail=f"Payment must be in NGN (got {currency})."
        )

    row, seat = await _record_founding_member(
        db, member_auth_id, body.paystack_reference, amount_kobo
    )
    logger.info(
        "Stroke Lab founding member %d claimed by %s (ref=%s)",
        seat,
        member_auth_id,
        body.paystack_reference,
    )
    return FoundingClaimResponse(
        seat_number=seat,
        paystack_reference=row.paystack_reference,
        amount_paid_kobo=row.amount_paid_kobo,
    )


# ── Internal route (payments_service → here) ─────────────────────


@internal_router.post(
    "/confirm",
    response_model=FoundingConfirmResponse,
    dependencies=[Depends(require_service_role)],
    summary="Record a founding member after payment clears (webhook-driven)",
)
async def confirm_founding_member(
    body: FoundingConfirmRequest,
    db: AsyncSession = Depends(get_async_db),
) -> FoundingConfirmResponse:
    try:
        member_auth_id = uuid.UUID(body.member_auth_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid member_auth_id") from exc

    already = await _existing_for_caller(db, member_auth_id)
    row, seat = await _record_founding_member(
        db, member_auth_id, body.payment_reference, body.amount_kobo
    )
    return FoundingConfirmResponse(recorded=already is None, seat_number=seat)
