"""High-level helpers for the payments service.

Includes the store-payment flow plus the Paystack proxy helpers that other
services use instead of importing PaystackClient directly (payments_service
owns PAYSTACK_SECRET_KEY).
"""

from __future__ import annotations

from typing import Optional

import httpx

from libs.common.config import get_settings

from .core import internal_get, internal_post

# ---------------------------------------------------------------------------
# Store payment flow
# ---------------------------------------------------------------------------


async def initialize_store_payment(
    order_id: str,
    *,
    amount_ngn: float,
    member_auth_id: str,
    member_email: str,
    order_number: str,
    callback_url: str | None = None,
    calling_service: str,
) -> dict:
    """Initialize a Paystack transaction for a store order via payments_service.

    Returns dict with {reference, authorization_url, access_code}.
    Raises httpx errors on failure.
    """
    settings = get_settings()
    reference = f"store-order-{order_id}"
    resp = await internal_post(
        service_url=settings.PAYMENTS_SERVICE_URL,
        path="/internal/payments/initialize",
        calling_service=calling_service,
        json={
            "purpose": "store_order",
            "amount": amount_ngn,
            "currency": "NGN",
            "reference": reference,
            "member_auth_id": member_auth_id,
            "callback_url": callback_url,
            "metadata": {
                "payer_email": member_email,
                "order_id": order_id,
                "order_number": order_number,
            },
        },
    )
    resp.raise_for_status()
    return resp.json()


async def verify_store_payment(
    reference: str,
    *,
    calling_service: str,
) -> dict:
    """Verify a Paystack payment reference via payments_service.

    Returns dict with {reference, status, provider_status, paid_at, amount_kobo, currency}.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.PAYMENTS_SERVICE_URL,
        path=f"/internal/payments/paystack/verify/{reference}",
        calling_service=calling_service,
    )
    resp.raise_for_status()
    return resp.json()


async def validate_discount_code(
    code: str,
    *,
    purpose: str = "store_order",
    amount: float = 0,
    member_auth_id: str | None = None,
    calling_service: str,
) -> Optional[dict]:
    """Validate a discount code via payments_service.

    Returns dict with {valid, discount_type, value, discount_amount, code, message}
    or None if the payments service is unreachable.
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.PAYMENTS_SERVICE_URL,
        path="/internal/payments/discounts/validate",
        calling_service=calling_service,
        json={
            "code": code,
            "purpose": purpose,
            "amount": amount,
            "member_auth_id": member_auth_id,
        },
    )
    if resp.status_code == 400:
        return {
            "valid": False,
            "message": resp.json().get("detail", "Invalid discount code"),
        }
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Paystack proxy
# ---------------------------------------------------------------------------


class PaystackProxyError(Exception):
    """Raised when an internal Paystack proxy call fails.

    Carries the upstream HTTP status and an actionable message that callers
    can surface to end users (e.g. "Could not verify bank account: ...").
    """

    def __init__(self, message: str, status_code: int = 502):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _proxy_error_from(resp: httpx.Response) -> PaystackProxyError:
    """Build a PaystackProxyError from a non-2xx httpx response."""
    try:
        body = resp.json()
        detail = body.get("detail") if isinstance(body, dict) else None
    except Exception:
        detail = None
    return PaystackProxyError(
        message=detail or f"Paystack proxy returned HTTP {resp.status_code}",
        status_code=resp.status_code,
    )


async def paystack_list_banks(
    *, calling_service: str, country: str = "nigeria"
) -> list[dict]:
    """List banks supported by Paystack via the payments-service proxy.

    Returns: list of {name, code, slug}.
    Raises: PaystackProxyError on non-2xx (lets callers fall back gracefully).
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.PAYMENTS_SERVICE_URL,
        path="/internal/payments/paystack/banks",
        calling_service=calling_service,
        params={"country": country},
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise _proxy_error_from(resp)
    return resp.json()


async def paystack_resolve_account(
    *,
    account_number: str,
    bank_code: str,
    calling_service: str,
) -> dict:
    """Resolve a bank account via the payments-service proxy.

    Returns: {account_number, account_name, bank_code}.
    Raises: PaystackProxyError on non-2xx.
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.PAYMENTS_SERVICE_URL,
        path="/internal/payments/paystack/resolve-account",
        calling_service=calling_service,
        json={"account_number": account_number, "bank_code": bank_code},
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise _proxy_error_from(resp)
    return resp.json()


async def paystack_create_recipient(
    *,
    name: str,
    account_number: str,
    bank_code: str,
    calling_service: str,
    currency: str = "NGN",
) -> dict:
    """Create a Paystack transfer recipient via the payments-service proxy.

    Returns: {recipient_code, name, account_number, bank_code, bank_name}.
    Raises: PaystackProxyError on non-2xx.
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.PAYMENTS_SERVICE_URL,
        path="/internal/payments/paystack/recipients",
        calling_service=calling_service,
        json={
            "name": name,
            "account_number": account_number,
            "bank_code": bank_code,
            "currency": currency,
        },
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise _proxy_error_from(resp)
    return resp.json()
