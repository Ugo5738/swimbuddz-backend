"""Gumroad integration for the public Stroke Lab analyzer paywall.

Config + the license-verify call. Gumroad has NO webhook signature, so the
webhook handler layers three checks (design §7.2): an unguessable shared-secret
path token (``GUMROAD_PING_TOKEN``), a ``seller_id`` match, and a MANDATORY
license re-verify here before any credit is granted. The access token is NOT
needed for verify (only for refund resource-subscriptions, which we don't
register from code).
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
from libs.common.logging import get_logger

logger = get_logger(__name__)

GUMROAD_VERIFY_URL = os.environ.get(
    "GUMROAD_VERIFY_URL", "https://api.gumroad.com/v2/licenses/verify"
)
# Our own shared secret appended to the Ping URL (the HMAC analog) — NOT a
# Gumroad-issued value. Set in env + pasted into the Gumroad dashboard Ping URL.
GUMROAD_PING_TOKEN = os.environ.get("GUMROAD_PING_TOKEN", "")
# Our Gumroad account id, posted in every Ping (a cheap sanity filter, NOT a
# secret — it appears in public Gumroad URLs).
GUMROAD_SELLER_ID = os.environ.get("GUMROAD_SELLER_ID", "")

_VERIFY_TIMEOUT = 10.0


async def verify_license(permalink: str, license_key: str) -> Optional[dict]:
    """Verify a license against Gumroad. Returns the ``purchase`` dict on
    success (carries ``sale_id``, ``email``, ``refunded``, ...), else None. No
    access token required (design §7.4). ``increment_uses_count=false`` so a
    balance check doesn't burn a use."""
    if not permalink or not license_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT) as client:
            resp = await client.post(
                GUMROAD_VERIFY_URL,
                data={
                    "product_permalink": permalink,
                    "license_key": license_key,
                    "increment_uses_count": "false",
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("Gumroad verify call failed: %s", exc)
        return None
    if resp.status_code != 200:
        logger.info(
            "Gumroad verify non-200 (%s) for permalink=%s", resp.status_code, permalink
        )
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    if not body.get("success"):
        return None
    purchase = body.get("purchase") or {}
    # A license stays verifiable AFTER a refund/dispute/chargeback — Gumroad just
    # flags the purchase. Never grant credits for a reversed sale (this single
    # gate covers both the webhook grant and the license-redeem path).
    if (
        purchase.get("refunded")
        or purchase.get("disputed")
        or purchase.get("chargebacked")
    ):
        logger.info("Gumroad verify: sale reversed (refunded/disputed/chargebacked)")
        return None
    return purchase
