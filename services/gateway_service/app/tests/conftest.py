"""Gateway test fixtures.

The gateway has no database; tests exercise the FastAPI app directly with
service clients swapped for fakes, so the only fixture needed is an ASGI
test client bound to the app.
"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from services.gateway_service.app.main import app


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client
