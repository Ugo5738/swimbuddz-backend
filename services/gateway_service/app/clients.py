"""HTTP clients for gateway to call microservices."""

import asyncio
from typing import Dict, Optional

import httpx

from libs.common.config import get_settings

settings = get_settings()


# A single pooled client shared across all ServiceClient instances. Reusing one
# client keeps TCP connections (keep-alive) warm between proxied calls instead of
# paying a fresh connect + pool setup on every request. Created lazily so it
# binds to the running event loop, and recreated if it was closed (e.g. between
# test event loops). `follow_redirects` and `timeout` are overridden per request
# so the media presign relay can still opt out of redirect-following.
_shared_client: Optional[httpx.AsyncClient] = None


def _get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
        )
    return _shared_client


async def aclose_shared_client() -> None:
    """Close the shared client. Call on gateway shutdown (best-effort)."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


class ServiceClient:
    """Base client for making HTTP requests to microservices."""

    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base_url = base_url
        self.timeout = timeout

    async def _request(
        self,
        method: str,
        path: str,
        *,
        follow_redirects: bool = True,
        **kwargs,
    ) -> httpx.Response:
        """Send a request with small retries to smooth over transient DNS/connection hiccups."""
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                client = _get_shared_client()
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    timeout=self.timeout,
                    follow_redirects=follow_redirects,
                    **kwargs,
                )
                # A redirect we deliberately did not follow is a valid
                # result for the caller to relay, not an error.
                if not (response.is_redirect and not follow_redirects):
                    response.raise_for_status()
                return response
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                # Only retry connection/read timeouts/errors; propagate HTTP errors immediately.
                if isinstance(exc, httpx.HTTPStatusError):
                    raise
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                raise
        # Should not reach here
        if last_exc:
            raise last_exc
        raise RuntimeError("Request failed without exception")

    async def get(
        self,
        path: str,
        headers: Optional[Dict] = None,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        """Make GET request to service."""
        return await self._request(
            "GET", path, headers=headers or {}, follow_redirects=follow_redirects
        )

    async def head(self, path: str, headers: Optional[Dict] = None) -> httpx.Response:
        """Make HEAD request to service."""
        return await self._request("HEAD", path, headers=headers or {})

    async def post(
        self,
        path: str,
        json: Optional[Dict] = None,
        content: Optional[bytes] = None,
        files: Optional[Dict] = None,
        headers: Optional[Dict] = None,
    ) -> httpx.Response:
        """Make POST request to service."""
        return await self._request(
            "POST",
            path,
            json=json,
            content=content,
            files=files,
            headers=headers or {},
        )

    async def put(
        self,
        path: str,
        json: Optional[Dict] = None,
        content: Optional[bytes] = None,
        headers: Optional[Dict] = None,
    ) -> httpx.Response:
        """Make PUT request to service."""
        return await self._request(
            "PUT",
            path,
            json=json,
            content=content,
            headers=headers or {},
        )

    async def patch(
        self,
        path: str,
        json: Optional[Dict] = None,
        content: Optional[bytes] = None,
        headers: Optional[Dict] = None,
    ) -> httpx.Response:
        """Make PATCH request to service."""
        return await self._request(
            "PATCH",
            path,
            json=json,
            content=content,
            headers=headers or {},
        )

    async def delete(self, path: str, headers: Optional[Dict] = None) -> httpx.Response:
        """Make DELETE request to service."""
        return await self._request("DELETE", path, headers=headers or {})


# Service client instances
members_client = ServiceClient(settings.MEMBERS_SERVICE_URL)
sessions_client = ServiceClient(settings.SESSIONS_SERVICE_URL)
attendance_client = ServiceClient(settings.ATTENDANCE_SERVICE_URL)
communications_client = ServiceClient(settings.COMMUNICATIONS_SERVICE_URL)
payments_client = ServiceClient(settings.PAYMENTS_SERVICE_URL)
academy_client = ServiceClient(settings.ACADEMY_SERVICE_URL)
media_client = ServiceClient(settings.MEDIA_SERVICE_URL)
events_client = ServiceClient(settings.EVENTS_SERVICE_URL)
transport_client = ServiceClient(settings.TRANSPORT_SERVICE_URL)
store_client = ServiceClient(settings.STORE_SERVICE_URL)
ai_client = ServiceClient(settings.AI_SERVICE_URL)
volunteer_client = ServiceClient(settings.VOLUNTEER_SERVICE_URL)
wallet_client = ServiceClient(settings.WALLET_SERVICE_URL)
pools_client = ServiceClient(settings.POOLS_SERVICE_URL)
reporting_client = ServiceClient(settings.REPORTING_SERVICE_URL)
chat_client = ServiceClient(settings.CHAT_SERVICE_URL)
corporate_client = ServiceClient(settings.CORPORATE_SERVICE_URL)
ledger_client = ServiceClient(settings.LEDGER_SERVICE_URL)
