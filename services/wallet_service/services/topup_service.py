"""Topup flow — initiating Paystack payments and confirming Bubble credits."""

import random
import string
import uuid

from fastapi import HTTPException, status
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import internal_get, internal_post
from services.wallet_service.models import (
    PaymentMethod,
    TopupStatus,
    TransactionType,
    WalletTopup,
)
from services.wallet_service.services.wallet_ops import (
    NAIRA_PER_BUBBLE,
    credit_wallet,
    get_wallet_by_auth_id,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)
settings = get_settings()


def _generate_topup_reference() -> str:
    """Generate a unique topup reference like TOP-A1B2C."""
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"TOP-{suffix}"


async def initiate_topup(
    db: AsyncSession,
    *,
    member_auth_id: str,
    bubbles_amount: int,
    payment_method: PaymentMethod = PaymentMethod.PAYSTACK,
    payer_email: str | None = None,
) -> WalletTopup:
    """Initiate a Bubble purchase.

    Flow (design doc Section 6.2):
    1. Validate wallet exists and is active
    2. Calculate naira_amount = bubbles × exchange_rate
    3. Create WalletTopup record (status: pending)
    4. Call payments service to initialize Paystack transaction
    5. Store paystack_authorization_url on topup record
    6. Return topup with redirect URL
    """
    # 1. Validate wallet
    wallet = await get_wallet_by_auth_id(db, member_auth_id)

    # 2. Calculate
    naira_amount = bubbles_amount * NAIRA_PER_BUBBLE
    reference = _generate_topup_reference()

    # 3. Create topup record
    topup = WalletTopup(
        wallet_id=wallet.id,
        member_auth_id=member_auth_id,
        reference=reference,
        bubbles_amount=bubbles_amount,
        naira_amount=naira_amount,
        exchange_rate=NAIRA_PER_BUBBLE,
        payment_method=payment_method,
        status=TopupStatus.PENDING,
    )
    db.add(topup)
    await db.commit()
    await db.refresh(topup)

    # 4. Call payments service to initialize Paystack
    if payment_method == PaymentMethod.PAYSTACK:
        try:
            resp = await internal_post(
                service_url=settings.PAYMENTS_SERVICE_URL,
                path="/payments/internal/initialize",
                calling_service="wallet",
                json={
                    "purpose": "wallet_topup",
                    "amount": naira_amount,  # Naira (payments service converts to kobo)
                    "currency": "NGN",
                    "reference": reference,
                    "member_auth_id": member_auth_id,
                    "callback_url": f"/account/wallet?topup={topup.id}",
                    "metadata": {
                        "topup_id": str(topup.id),
                        "wallet_id": str(wallet.id),
                        "bubbles_amount": bubbles_amount,
                        "type": "wallet_topup",
                        "payer_email": payer_email,
                    },
                },
            )

            if resp.status_code >= 400:
                logger.error(
                    "Payments service returned %d for topup %s: %s",
                    resp.status_code,
                    topup.id,
                    resp.text,
                )
                topup.status = TopupStatus.FAILED
                topup.failed_at = utc_now()
                topup.failure_reason = f"Payment init failed: {resp.status_code}"
                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Payment didn't go through. Please try again.",
                )

            data = resp.json()
            topup.payment_reference = data.get("reference", reference)
            topup.paystack_authorization_url = data.get("authorization_url")
            topup.paystack_access_code = data.get("access_code")
            topup.status = TopupStatus.PROCESSING
            await db.commit()
            await db.refresh(topup)

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to initialize Paystack for topup %s: %s", topup.id, e)
            topup.status = TopupStatus.FAILED
            topup.failed_at = utc_now()
            topup.failure_reason = str(e)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Payment didn't go through. Please try again.",
            )

    logger.info(
        "Initiated topup %s: %d bubbles (₦%d) for member %s",
        topup.id,
        bubbles_amount,
        naira_amount,
        member_auth_id,
    )
    return topup


async def confirm_topup(
    db: AsyncSession,
    *,
    topup_reference: str,
    payment_reference: str,
    payment_status: str,
) -> WalletTopup:
    """Confirm a topup after Paystack webhook.

    Called by payments service via POST /internal/wallet/confirm-topup.
    Idempotent — skips if topup already completed.
    """
    result = await db.execute(
        select(WalletTopup).where(WalletTopup.reference == topup_reference)
    )
    topup = result.scalar_one_or_none()
    if not topup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topup with reference {topup_reference} not found",
        )

    # Idempotent: already completed
    if topup.status == TopupStatus.COMPLETED:
        logger.info("Topup %s already completed, skipping", topup.id)
        return topup

    if payment_status != "completed":
        topup.status = TopupStatus.FAILED
        topup.failed_at = utc_now()
        topup.failure_reason = f"Payment status: {payment_status}"
        topup.payment_reference = payment_reference
        await db.commit()
        await db.refresh(topup)
        return topup

    # Credit wallet
    await credit_wallet(
        db,
        member_auth_id=topup.member_auth_id,
        amount=topup.bubbles_amount,
        idempotency_key=f"topup-{topup.id}",
        transaction_type=TransactionType.TOPUP,
        description=f"Added {topup.bubbles_amount} 🫧 via Paystack",
        service_source="wallet_service",
        reference_type="topup",
        reference_id=str(topup.id),
        initiated_by=topup.member_auth_id,
    )

    topup.status = TopupStatus.COMPLETED
    topup.payment_reference = payment_reference
    topup.completed_at = utc_now()
    await db.commit()
    await db.refresh(topup)

    logger.info(
        "Confirmed topup %s: credited %d bubbles to wallet %s",
        topup.id,
        topup.bubbles_amount,
        topup.wallet_id,
    )
    return topup


async def get_topup(
    db: AsyncSession, topup_id: uuid.UUID, member_auth_id: str
) -> WalletTopup:
    """Get a topup by ID, scoped to the requesting member."""
    result = await db.execute(
        select(WalletTopup).where(
            WalletTopup.id == topup_id,
            WalletTopup.member_auth_id == member_auth_id,
        )
    )
    topup = result.scalar_one_or_none()
    if not topup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Topup not found"
        )
    return topup


async def reconcile_topup_return(
    db: AsyncSession, *, topup_reference: str, member_auth_id: str
) -> WalletTopup:
    """Reconcile a wallet topup after frontend return.

    Webhooks remain the source of truth, but this provides a safe fallback:
    verify provider state and apply credit idempotently when webhook timing lags.
    """
    result = await db.execute(
        select(WalletTopup).where(
            WalletTopup.reference == topup_reference,
            WalletTopup.member_auth_id == member_auth_id,
        )
    )
    topup = result.scalar_one_or_none()
    if not topup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Topup not found"
        )

    if topup.status == TopupStatus.COMPLETED:
        return topup

    try:
        resp = await internal_get(
            service_url=settings.PAYMENTS_SERVICE_URL,
            path=f"/payments/internal/paystack/verify/{topup_reference}",
            calling_service="wallet",
        )
        if resp.status_code >= 400:
            logger.warning(
                "Topup verify failed for %s: http=%d body=%s",
                topup_reference,
                resp.status_code,
                resp.text,
            )
            return topup

        payload = resp.json()
        verify_status = payload.get("status")

        if verify_status == "completed":
            return await confirm_topup(
                db,
                topup_reference=topup_reference,
                payment_reference=topup_reference,
                payment_status="completed",
            )
        if verify_status == "failed":
            return await confirm_topup(
                db,
                topup_reference=topup_reference,
                payment_reference=topup_reference,
                payment_status="failed",
            )
    except Exception as exc:
        logger.warning(
            "Topup reconcile failed for %s: %s",
            topup_reference,
            exc,
        )

    await db.refresh(topup)
    return topup
