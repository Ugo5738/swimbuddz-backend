"""Reusable async HTTP client for internal service-to-service communication.

All cross-service calls should go through this helper instead of importing
models or querying tables from other services directly.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from libs.common.logging import get_logger, get_request_id

logger = get_logger(__name__)

# Default timeout for internal calls (seconds).
_DEFAULT_TIMEOUT = 10.0


async def internal_request(
    *,
    service_url: str,
    method: str,
    path: str,
    calling_service: str,
    json: Any = None,
    params: Optional[dict] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> httpx.Response:
    """Make an authenticated internal service-to-service HTTP call.

    Args:
        service_url: Base URL of the target service (e.g. settings.MEMBERS_SERVICE_URL).
        method: HTTP method (GET, POST, DELETE, …).
        path: URL path on the target service (e.g. "/internal/members/by-auth/abc").
        calling_service: Name of the calling service for the JWT "sub" claim.
        json: Optional JSON body.
        params: Optional query parameters.
        timeout: Request timeout in seconds.

    Returns:
        The httpx.Response object.

    Raises:
        httpx.RequestError on connection failures.
    """
    url = f"{service_url}{path}"
    headers = {"Authorization": f"Bearer {_service_role_jwt(calling_service)}"}
    request_id = get_request_id()
    if request_id:
        headers["X-Request-ID"] = request_id
    headers["X-Caller-Service"] = calling_service

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            json=json,
            params=params,
        )
    return response


async def internal_get(
    *,
    service_url: str,
    path: str,
    calling_service: str,
    params: Optional[dict] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> httpx.Response:
    """Convenience wrapper for GET requests."""
    return await internal_request(
        service_url=service_url,
        method="GET",
        path=path,
        calling_service=calling_service,
        params=params,
        timeout=timeout,
    )


async def internal_post(
    *,
    service_url: str,
    path: str,
    calling_service: str,
    json: Any = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> httpx.Response:
    """Convenience wrapper for POST requests."""
    return await internal_request(
        service_url=service_url,
        method="POST",
        path=path,
        calling_service=calling_service,
        json=json,
        timeout=timeout,
    )


async def internal_delete(
    *,
    service_url: str,
    path: str,
    calling_service: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> httpx.Response:
    """Convenience wrapper for DELETE requests."""
    return await internal_request(
        service_url=service_url,
        method="DELETE",
        path=path,
        calling_service=calling_service,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# High-level helpers (resolve common cross-service lookups)
# ---------------------------------------------------------------------------


async def get_member_by_auth_id(
    auth_id: str, *, calling_service: str
) -> Optional[dict]:
    """Look up a member by their Supabase auth_id.

    Returns dict with {id, first_name, last_name, email} or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/internal/members/by-auth/{auth_id}",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_member_by_id(member_id: str, *, calling_service: str) -> Optional[dict]:
    """Look up a member by their member ID.

    Returns dict with {id, first_name, last_name, email} or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/internal/members/{member_id}",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_members_bulk(
    member_ids: list[str], *, calling_service: str
) -> list[dict]:
    """Bulk-lookup members by IDs.

    Returns list of {id, first_name, last_name, email}.
    """
    if not member_ids:
        return []
    settings = get_settings()
    resp = await internal_post(
        service_url=settings.MEMBERS_SERVICE_URL,
        path="/internal/members/bulk",
        calling_service=calling_service,
        json={"ids": member_ids},
    )
    resp.raise_for_status()
    return resp.json()


async def get_coach_profile(member_id: str, *, calling_service: str) -> Optional[dict]:
    """Look up coach profile by member_id.

    Returns dict with {member_id, status, academy_cohort_stipend, ...} or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/internal/coaches/{member_id}/profile",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_session_by_id(session_id: str, *, calling_service: str) -> Optional[dict]:
    """Look up a session by ID.

    Returns dict with session details or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.SESSIONS_SERVICE_URL,
        path=f"/internal/sessions/{session_id}",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_next_session_for_cohort(
    cohort_id: str, *, calling_service: str
) -> Optional[dict]:
    """Get the next upcoming session for a cohort.

    Returns dict with {starts_at, title, location_name} or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.SESSIONS_SERVICE_URL,
        path=f"/internal/cohorts/{cohort_id}/next-session",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_session_ids_for_cohort(
    cohort_id: str, *, calling_service: str
) -> list[str]:
    """Get all session IDs for a cohort.

    Returns list of session ID strings.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.SESSIONS_SERVICE_URL,
        path=f"/internal/cohorts/{cohort_id}/session-ids",
        calling_service=calling_service,
    )
    resp.raise_for_status()
    return resp.json()


async def get_coach_readiness_data(
    member_id: str, *, calling_service: str
) -> Optional[dict]:
    """Get extended coach profile data for readiness assessment.

    Returns dict with {profile_id, total_coaching_hours, average_rating,
    background_check_status, has_cpr_training, cpr_expiry_date, has_active_agreement}
    or None.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/internal/coaches/{member_id}/readiness",
        calling_service=calling_service,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


async def get_eligible_coaches(
    grade_column: str,
    eligible_grades: list[str],
    *,
    calling_service: str,
) -> list[dict]:
    """Get eligible coaches filtered by grade column and allowed grades.

    Returns list of {member_id, name, email, grade, total_coaching_hours, average_feedback_rating}.
    """
    if not eligible_grades:
        return []
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path="/internal/coaches/eligible",
        calling_service=calling_service,
        params={
            "grade_column": grade_column,
            "eligible_grades": ",".join(eligible_grades),
        },
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Wallet Service helpers
# ---------------------------------------------------------------------------


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
