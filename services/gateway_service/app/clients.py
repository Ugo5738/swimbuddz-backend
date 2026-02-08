"""HTTP clients for gateway to call microservices."""

import asyncio
from typing import Dict, Optional

import httpx
from libs.common.config import get_settings

settings = get_settings()


class ServiceClient:
    """Base client for making HTTP requests to microservices."""

    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base_url = base_url
        self.timeout = timeout

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Send a request with small retries to smooth over transient DNS/connection hiccups."""
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.request(
                        method, f"{self.base_url}{path}", **kwargs
                    )
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

    async def get(self, path: str, headers: Optional[Dict] = None) -> httpx.Response:
        """Make GET request to service."""
        return await self._request("GET", path, headers=headers or {})

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
