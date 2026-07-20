"""Admin payment endpoints.

* DELETE /payments/admin/members/by-auth/{auth_id} — purge all
  payments for a member (used by GDPR / test cleanup tooling).
* POST /payments/admin/{reference}/replay-entitlement — re-run
  entitlement fulfillment for a PAID payment whose previous apply
  failed (used during incident response).
* POST /payments/admin/bookings/{booking_id}/payment-link — generate
  a Paystack checkout URL for an outstanding session-fee booking
  (walk-in flow). Admin sends the URL to the member via WhatsApp /
  email; payment goes through the same SESSION_BOOKING entitlement
  flow as a member-initiated pay.
* POST /payments/admin/bookings/{booking_id}/offline-payment — record a
  full session fee collected by bank transfer, cash, POS, or another
  verified off-platform method.
"""

import uuid
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import get_booking_by_id, get_member_by_id
from libs.db.session import get_async_db
from services.payments_service.models import (
    Payment,
    PaymentPurpose,
    PaymentStatus,
)
from services.payments_service.schemas import (
    PaymentResponse,
)

from ._paystack import _initialize_paystack, _paystack_enabled

settings = get_settings()
logger = get_logger(__name__)

FULFILLMENT_META_KEY = "fulfillment"
MAX_FULFILLMENT_RETRIES = 8
BASE_FULFILLMENT_RETRY_MINUTES = 2

from ._entitlement import _apply_entitlement_with_tracking, _mark_paid_and_apply

router = APIRouter()


@router.delete("/admin/members/by-auth/{auth_id}")
async def admin_delete_member_payments(
    auth_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete payments for a member by auth ID (Admin only).
    """
    result = await db.execute(delete(Payment).where(Payment.member_auth_id == auth_id))
    await db.commit()
    return {"deleted": result.rowcount or 0}


@router.post("/admin/{reference}/replay-entitlement", response_model=PaymentResponse)
async def replay_payment_entitlement(
    reference: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Replay entitlement fulfillment for a paid payment."""
    result = await db.execute(select(Payment).where(Payment.reference == reference))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )
    if payment.status != PaymentStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment is not paid (status={payment.status.value})",
        )

    await _apply_entitlement_with_tracking(payment)
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Entitlement replay requested by %s for payment %s",
        current_user.user_id,
        reference,
    )
    return payment


class AdminBookingPayLinkRequest(BaseModel):
    """Optional overrides when generating an admin pay-link for a booking."""

    amount_naira: Optional[float] = None  # Default = booking.fee / 100
    note: Optional[str] = None  # Free-form admin note attached to the payment


class AdminBookingPayLinkResponse(BaseModel):
    reference: str
    authorization_url: str
    payer_email: str
    amount: float
    booking_id: str
    session_id: str

    model_config = ConfigDict(from_attributes=False)


class AdminBookingOfflinePaymentRequest(BaseModel):
    """Auditable receipt details for a session fee collected off-platform."""

    model_config = ConfigDict(extra="forbid")

    payment_method: Literal["bank_transfer", "cash", "pos", "other"] = "bank_transfer"
    received_at: Optional[datetime] = None
    external_reference: Optional[str] = Field(default=None, max_length=128)
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("external_reference", "note")
    @classmethod
    def strip_optional_text(cls, value: Optional[str]) -> Optional[str]:
        cleaned = (value or "").strip()
        return cleaned or None

    @model_validator(mode="after")
    def validate_receipt_details(self):
        if (
            self.payment_method in {"bank_transfer", "pos"}
            and not (self.external_reference or "").strip()
        ):
            raise ValueError(
                "external_reference is required for bank transfer and POS payments"
            )
        if self.payment_method == "other" and not (self.note or "").strip():
            raise ValueError("note is required when payment_method is other")
        return self


async def _find_paid_booking_payment(
    db: AsyncSession, booking_id: uuid.UUID
) -> Payment | None:
    result = await db.execute(
        select(Payment)
        .where(
            Payment.purpose == PaymentPurpose.SESSION_BOOKING,
            Payment.status == PaymentStatus.PAID,
            or_(
                Payment.session_booking_id == booking_id,
                Payment.payment_metadata["booking_id"].astext == str(booking_id),
            ),
        )
        .order_by(Payment.paid_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.post(
    "/admin/bookings/{booking_id}/offline-payment",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_record_booking_offline_payment(
    booking_id: uuid.UUID,
    payload: AdminBookingOfflinePaymentRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Record a full session fee collected outside SwimBuddz checkout.

    The booking's pricing snapshot is authoritative. The endpoint deliberately
    does not accept a client total or partial payment because the booking model
    currently represents one fully settled fee. The resulting Payment follows
    the normal entitlement and ledger path and backfills payment_intent_id on
    an already-confirmed walk-in booking.
    """
    booking = await get_booking_by_id(str(booking_id), calling_service="payments")
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    booking_status = str(booking.get("status") or "").lower()
    if booking_status not in {"pending", "confirmed"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Booking status is '{booking_status}'. Only pending or confirmed "
                "bookings can be settled."
            ),
        )

    fee_kobo = int(booking.get("fee_amount_kobo") or 0)
    if fee_kobo <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking has no outstanding session fee.",
        )

    existing_paid = await _find_paid_booking_payment(db, booking_id)
    if existing_paid is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This booking is already settled by payment "
                f"{existing_paid.reference}."
            ),
        )

    if payload.external_reference:
        duplicate_receipt = (
            await db.execute(
                select(Payment)
                .where(
                    Payment.status == PaymentStatus.PAID,
                    Payment.provider == "offline",
                    func.lower(Payment.provider_reference)
                    == payload.external_reference.lower(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if duplicate_receipt is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This external payment reference is already recorded as "
                    f"{duplicate_receipt.reference}."
                ),
            )

    member_id = booking.get("member_id")
    member_auth_id = booking.get("member_auth_id")
    session_id = booking.get("session_id")
    if not member_id or not member_auth_id or not session_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Booking is missing member or session identifiers.",
        )

    member = await get_member_by_id(str(member_id), calling_service="payments")
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    received_at = payload.received_at or utc_now()
    if received_at.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="received_at must include a timezone",
        )
    if received_at > utc_now() + timedelta(minutes=5):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="received_at cannot be in the future",
        )

    payment = Payment(
        reference=Payment.generate_reference(),
        member_auth_id=str(member_auth_id),
        payer_email=member.get("email"),
        purpose=PaymentPurpose.SESSION_BOOKING,
        amount=fee_kobo / 100,
        currency="NGN",
        status=PaymentStatus.PENDING,
        payment_method=payload.payment_method,
        session_booking_id=booking_id,
        admin_review_note=payload.note,
        payment_metadata={
            "booking_id": str(booking_id),
            "session_id": str(session_id),
            "member_id": str(member_id),
            "recorded_offline": True,
            "recorded_by_auth_id": current_user.user_id,
            "recorded_by_email": current_user.email,
            "external_reference": payload.external_reference,
            "admin_note": payload.note,
        },
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    payment = await _mark_paid_and_apply(
        db,
        payment,
        provider="offline",
        provider_reference=payload.external_reference,
        paid_at=received_at,
        provider_payload={
            "payment_method": payload.payment_method,
            "recorded_by_auth_id": current_user.user_id,
        },
    )
    logger.info(
        "Admin %s recorded offline payment %s for booking %s via %s",
        current_user.user_id,
        payment.reference,
        booking_id,
        payload.payment_method,
    )
    return payment


@router.post(
    "/admin/bookings/{booking_id}/payment-link",
    response_model=AdminBookingPayLinkResponse,
)
async def admin_generate_booking_pay_link(
    booking_id: uuid.UUID,
    payload: AdminBookingPayLinkRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Generate a Paystack checkout link for an outstanding-fee booking.

    Looks up the booking + member via the sessions/members services,
    inserts a PENDING SESSION_BOOKING payment row tied to the booking,
    initializes Paystack, and returns the authorization URL for the
    admin to forward to the member (WhatsApp / email / SMS).

    Validation:
      - booking exists, status=confirmed, fee_amount_kobo > 0
      - no existing PAID payment already references the booking_id

    Once the member pays, the standard webhook flow flips the payment to
    PAID and the session_booking entitlement handler backfills the
    booking's payment_intent_id (already-confirmed case).
    """
    if not _paystack_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paystack is not configured on this environment.",
        )

    # 1. Fetch the booking via sessions-service internal endpoint.
    booking = await get_booking_by_id(str(booking_id), calling_service="payments")
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    booking_status = str(booking.get("status") or "").lower()
    if booking_status != "confirmed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Booking status is '{booking_status}'. Only confirmed bookings "
                f"can have a pay-link generated."
            ),
        )
    fee_kobo = int(booking.get("fee_amount_kobo") or 0)
    if fee_kobo <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking has no outstanding fee (fee_amount_kobo is 0).",
        )
    member_id = booking.get("member_id")
    member_auth_id = booking.get("member_auth_id")
    session_id = booking.get("session_id")
    if not member_id or not member_auth_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Booking is missing member identifiers — cannot generate link.",
        )

    # 2. Resolve the member's email via members-service.
    member = await get_member_by_id(str(member_id), calling_service="payments")
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    payer_email = member.get("email")
    if not payer_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Member has no email on file — cannot initialize Paystack.",
        )

    # 3. Block duplicate pay links if a PAID payment already exists for this
    # booking (would create double-charge risk if admin re-sends an old link
    # after payment cleared but before they refreshed the UI).
    existing_paid = await _find_paid_booking_payment(db, booking_id)
    if existing_paid is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This booking has already been paid in full.",
        )

    # 4. Determine amount (default = booking fee, admin can override only
    # downward — overriding higher would be a tip / penalty surcharge that
    # should go through a dedicated endpoint).
    fee_naira = fee_kobo / 100
    if payload.amount_naira is not None:
        if payload.amount_naira <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="amount_naira must be greater than zero",
            )
        if payload.amount_naira > fee_naira:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"amount_naira {payload.amount_naira} exceeds the booking's "
                    f"outstanding fee {fee_naira}"
                ),
            )
        amount = float(payload.amount_naira)
    else:
        amount = fee_naira

    # 5. Create the PENDING payment row. Mirrors what intent_creation does
    # for SESSION_BOOKING purpose, with member_auth_id from the booking
    # (not from current_user — admin is acting on behalf of the member).
    payment_metadata = {
        "booking_id": str(booking_id),
        "session_id": str(session_id) if session_id else None,
        "admin_generated": True,
        "admin_auth_id": current_user.user_id,
    }
    if payload.note:
        payment_metadata["admin_note"] = payload.note

    payment = Payment(
        reference=Payment.generate_reference(),
        member_auth_id=member_auth_id,
        payer_email=payer_email,
        purpose=PaymentPurpose.SESSION_BOOKING,
        amount=amount,
        currency="NGN",
        status=PaymentStatus.PENDING,
        payment_method="paystack",
        session_booking_id=booking_id,
        payment_metadata=payment_metadata,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    # 6. Initialize the Paystack transaction & persist the URL on the row.
    authorization_url, access_code = await _initialize_paystack(
        payment, payer_email, redirect_path=None
    )
    if not authorization_url:
        # Roll back the pending row — no point leaving a dead reference.
        await db.delete(payment)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Paystack did not return a checkout URL.",
        )

    payment.provider = "paystack"
    payment.provider_reference = payment.reference
    payment.payment_metadata = {
        **(payment.payment_metadata or {}),
        "paystack": {
            "authorization_url": authorization_url,
            "access_code": access_code,
        },
    }
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Admin %s generated pay-link %s for booking %s (member %s, amount %s)",
        current_user.user_id,
        payment.reference,
        booking_id,
        member_id,
        amount,
    )

    return AdminBookingPayLinkResponse(
        reference=payment.reference,
        authorization_url=authorization_url,
        payer_email=payer_email,
        amount=amount,
        booking_id=str(booking_id),
        session_id=str(session_id) if session_id else "",
    )
