"""High-level helpers for the wallet service.

Covers balance lookups, Bubble debits/credits, reward grants, and the
rewards-engine event emitter.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger

from .core import internal_get, internal_post

logger = get_logger(__name__)


async def get_wallet_balance(auth_id: str, *, calling_service: str) -> Optional[dict]:
    """Look up a member's wallet balance.

    Returns dict with {wallet_id, member_auth_id, balance, status} or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.WALLET_SERVICE_URL,
        path=f"/internal/wallet/balance/{auth_id}",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def grant_pool_submission_reward(
    *,
    member_auth_id: str,
    bubbles_amount: int,
    submission_id: str,
    granted_by: str,
    calling_service: str,
) -> dict:
    """Grant Bubbles to a member for an approved pool submission.

    Called by pools_service after admin approval. Idempotent via submission_id.
    Returns the GrantResponse dict from wallet_service.
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.WALLET_SERVICE_URL,
        path="/internal/wallet/pool-submission-reward",
        calling_service=calling_service,
        json={
            "member_auth_id": member_auth_id,
            "bubbles_amount": bubbles_amount,
            "submission_id": submission_id,
            "granted_by": granted_by,
        },
    )
    resp.raise_for_status()
    return resp.json()


async def grant_challenge_reward_bubbles(
    *,
    member_auth_id: str,
    bubbles_amount: int,
    submission_id: str,
    member_id: str,
    granted_by: str,
    calling_service: str,
) -> dict:
    """Grant Bubbles to a member for an approved challenge submission.

    Called by members_service after admin approves a submission. Idempotent
    via the per-member campaign code `CHALLENGE_{submission_id}_{member_id}` —
    the per-member component is critical because team submissions trigger
    one grant per member, all sharing submission_id.
    Returns the GrantResponse dict from wallet_service.
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.WALLET_SERVICE_URL,
        path="/internal/wallet/challenge-completion-reward",
        calling_service=calling_service,
        json={
            "member_auth_id": member_auth_id,
            "bubbles_amount": bubbles_amount,
            "submission_id": submission_id,
            "member_id": member_id,
            "granted_by": granted_by,
        },
    )
    resp.raise_for_status()
    return resp.json()


async def debit_member_wallet(
    auth_id: str,
    *,
    amount: int,
    idempotency_key: str,
    description: str,
    calling_service: str,
    transaction_type: str = "purchase",
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
) -> dict:
    """Debit Bubbles from a member's wallet.

    Returns dict with {success, transaction_id, balance_after}.
    Raises httpx errors on failure.
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.WALLET_SERVICE_URL,
        path="/internal/wallet/debit",
        calling_service=calling_service,
        json={
            "idempotency_key": idempotency_key,
            "member_auth_id": auth_id,
            "amount": amount,
            "transaction_type": transaction_type,
            "description": description,
            "service_source": calling_service,
            "reference_type": reference_type,
            "reference_id": reference_id,
        },
    )
    resp.raise_for_status()
    return resp.json()


async def credit_member_wallet(
    auth_id: str,
    *,
    amount: int,
    idempotency_key: str,
    description: str,
    calling_service: str,
    transaction_type: str = "refund",
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
) -> dict:
    """Credit Bubbles to a member's wallet.

    Returns dict with {success, transaction_id, balance_after}.
    Raises httpx errors on failure.
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.WALLET_SERVICE_URL,
        path="/internal/wallet/credit",
        calling_service=calling_service,
        json={
            "idempotency_key": idempotency_key,
            "member_auth_id": auth_id,
            "amount": amount,
            "transaction_type": transaction_type,
            "description": description,
            "service_source": calling_service,
            "reference_type": reference_type,
            "reference_id": reference_id,
        },
    )
    resp.raise_for_status()
    return resp.json()


async def check_wallet_balance(
    auth_id: str,
    *,
    required_amount: int,
    calling_service: str,
) -> Optional[dict]:
    """Check if a member has sufficient Bubbles.

    Returns dict with {sufficient, current_balance, required_amount, wallet_status}.
    """
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.WALLET_SERVICE_URL,
        path="/internal/wallet/check-balance",
        calling_service=calling_service,
        json={
            "member_auth_id": auth_id,
            "required_amount": required_amount,
        },
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def emit_rewards_event(
    *,
    event_type: str,
    member_auth_id: str,
    service_source: str,
    event_data: dict,
    idempotency_key: str,
    calling_service: str,
    member_id: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> Optional[dict]:
    """Emit an event to the rewards engine for automatic Bubble rewards.

    Best-effort: catches all exceptions and returns None on failure so the
    calling operation is never blocked by rewards processing.

    Returns dict with {event_id, accepted, rewards_granted, rewards} on success.
    """
    settings = get_settings()
    try:
        resp = await internal_post(
            service_url=settings.WALLET_SERVICE_URL,
            path="/internal/wallet/events",
            calling_service=calling_service,
            json={
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "member_auth_id": member_auth_id,
                "member_id": member_id,
                "service_source": service_source,
                "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
                "event_data": event_data,
                "idempotency_key": idempotency_key,
            },
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.warning(
            "Failed to emit rewards event %s for %s (best-effort, continuing)",
            event_type,
            member_auth_id,
            exc_info=True,
        )
        return None
