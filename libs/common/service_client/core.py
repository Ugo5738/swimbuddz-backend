"""Core HTTP helpers for internal service-to-service communication.

All cross-service calls should go through `internal_request` (or one of the
verb-specific wrappers) instead of importing models or querying tables from
other services directly.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from libs.auth.dependencies import _service_role_jwt
from libs.common.logging import get_request_id

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


async def internal_patch(
    *,
    service_url: str,
    path: str,
    calling_service: str,
    json: Any = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> httpx.Response:
    """Convenience wrapper for PATCH requests."""
    return await internal_request(
        service_url=service_url,
        method="PATCH",
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
