"""Paystack-specific helpers: enablement, HMAC verification, currency,
and the two outbound Paystack API calls (`/transaction/initialize` and
`/transaction/verify/{ref}`).

Imported by intent_creation, member_payments, the webhook handler in
`routers/webhooks.py`, and the internal Paystack proxy in
`routers/internal.py`.
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


def _paystack_enabled() -> bool:
    key = (settings.PAYSTACK_SECRET_KEY or "").strip()
    return bool(key) and not key.startswith("your-")


def _paystack_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _to_kobo(amount: float) -> int:
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(value * KOBO_PER_NAIRA)


def _verify_paystack_signature(raw_body: bytes, signature: str) -> bool:
    secret = (settings.PAYSTACK_SECRET_KEY or "").encode("utf-8")
    digest = hmac.new(secret, raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature)


def _callback_url(reference: str, redirect_path: str = None) -> str:
    # If a service provides a redirect path (e.g. wallet topup), always honor it.
    # PAYSTACK_CALLBACK_URL is treated as default only when no redirect is provided.
    if redirect_path:
        if redirect_path.startswith(("http://", "https://")):
            callback = redirect_path
        else:
            base = settings.FRONTEND_URL.rstrip("/")
            path = (
                redirect_path if redirect_path.startswith("/") else f"/{redirect_path}"
            )
            callback = f"{base}{path}"
    elif settings.PAYSTACK_CALLBACK_URL:
        callback = settings.PAYSTACK_CALLBACK_URL
    else:
        base = settings.FRONTEND_URL.rstrip("/")
        callback = f"{base}/account/billing"

    # Ensure provider marker is present for frontend return handling.
    parts = urlsplit(callback)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("provider", "paystack")
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


async def _initialize_paystack(
    payment: Payment, email: str, redirect_path: str = None
) -> tuple[str | None, str | None]:
    """
    Initialize a Paystack transaction and return (authorization_url, access_code).
    """
    if not _paystack_enabled():
        return None, None

    payload = {
        "email": email,
        "amount": _to_kobo(payment.amount),
        "currency": payment.currency,
        "reference": payment.reference,
        "callback_url": _callback_url(payment.reference, redirect_path),
        "metadata": {
            "internal_reference": payment.reference,
            "purpose": str(payment.purpose),
            "member_auth_id": payment.member_auth_id,
        },
    }
    # In local/dev, limit channels to avoid flaky/unsupported methods (e.g. QR/Zap)
    # that can leave the checkout stuck on "transaction ongoing" without completing.
    if settings.ENVIRONMENT in ("local", "development"):
        payload["channels"] = ["card", "bank", "ussd", "bank_transfer"]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.PAYSTACK_API_BASE_URL.rstrip('/')}/transaction/initialize",
            headers=_paystack_headers(),
            json=payload,
        )

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack initialize failed ({resp.status_code}): {resp.text}",
        )

    body = resp.json()
    if not body.get("status"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack initialize failed: {body}",
        )

    data = body.get("data") or {}
    return data.get("authorization_url"), data.get("access_code")


async def _verify_paystack_transaction(
    reference: str, *, _max_retries: int = 3
) -> dict:
    if not _paystack_enabled():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Paystack is not configured.",
        )

    import asyncio
    import ssl as _ssl

    url = f"{settings.PAYSTACK_API_BASE_URL.rstrip('/')}/transaction/verify/{reference}"
    headers = _paystack_headers()

    for attempt in range(1, _max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers=headers)
            break  # success — exit retry loop
        except (_ssl.SSLError, httpx.ConnectError, httpx.ReadError) as exc:
            if attempt < _max_retries:
                logger.warning(
                    "Paystack verify attempt %d/%d failed (%s: %s), retrying...",
                    attempt,
                    _max_retries,
                    type(exc).__name__,
                    exc,
                )
                await asyncio.sleep(1.0 * attempt)  # 1s, 2s backoff
            else:
                logger.error(
                    "Paystack verify failed after %d attempts: %s",
                    _max_retries,
                    exc,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Paystack connection error after {_max_retries} retries: {exc}",
                ) from exc

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack verify failed ({resp.status_code}): {resp.text}",
        )

    body = resp.json()
    if not body.get("status"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack verify failed: {body}",
        )
    return body.get("data") or {}
