"""POST /payments/{reference}/complete — admin manual completion.

Marks a Payment PAID and triggers entitlement application. Guards
against duplicate `provider_reference` collisions across other
payments. Idempotent: already-PAID payments are returned unchanged.
"""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import _service_role_jwt, get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.currency import KOBO_PER_NAIRA
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import (
    dispatch_notification,
    emit_rewards_event,
    get_member_by_auth_id,
    internal_post,
)
from libs.db.session import get_async_db
from services.payments_service.models import (
    Discount,
    DiscountType,
    Payment,
    PaymentPurpose,
    PaymentStatus,
)
from services.payments_service.schemas import (
    ClubBillingCycle,
    CompletePaymentRequest,
    CreatePaymentIntentRequest,
    PaymentIntentResponse,
    PaymentResponse,
    PricingConfigResponse,
    SessionAttendanceRole,
    SessionAttendanceStatus,
)

settings = get_settings()
logger = get_logger(__name__)

FULFILLMENT_META_KEY = "fulfillment"
MAX_FULFILLMENT_RETRIES = 8
BASE_FULFILLMENT_RETRY_MINUTES = 2

from ._entitlement import _apply_entitlement_with_tracking

router = APIRouter()


@router.post("/{reference}/complete", response_model=PaymentResponse)
async def complete_payment(
    reference: str,
    payload: CompletePaymentRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Mark a payment as paid and apply the corresponding member entitlement.
    In production, this should be triggered by a verified payment webhook.
    """
    query = select(Payment).where(Payment.reference == reference)
    result = await db.execute(query)
    payment = result.scalar_one_or_none()

    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )

    if payment.status == PaymentStatus.PAID:
        return payment

    if payload.provider_reference:
        dupe_query = select(Payment).where(
            Payment.provider_reference == payload.provider_reference
        )
        dupe_result = await db.execute(dupe_query)
        dupe_payment = dupe_result.scalar_one_or_none()
        if dupe_payment and dupe_payment.id != payment.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="provider_reference already used by another payment",
            )

    payment.status = PaymentStatus.PAID
    payment.provider = payload.provider
    payment.provider_reference = payload.provider_reference
    payment.paid_at = payload.paid_at or datetime.now(timezone.utc)
    payment.entitlement_error = None

    if payload.note:
        payment.payment_metadata = {
            **(payment.payment_metadata or {}),
            "admin_note": payload.note,
        }

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    await _apply_entitlement_with_tracking(payment)

    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment
