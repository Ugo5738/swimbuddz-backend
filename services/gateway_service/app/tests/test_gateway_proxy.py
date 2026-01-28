import httpx
import pytest
from services.gateway_service.app import clients


class _FakeServiceClient:
    """Minimal stand-in for ServiceClient that returns a canned response."""

    def __init__(self, response: httpx.Response):
        self.response = response

    async def get(self, *_args, **_kwargs) -> httpx.Response:
        return self.response

    async def post(self, *_args, **_kwargs) -> httpx.Response:
        return self.response

    async def put(self, *_args, **_kwargs) -> httpx.Response:
        return self.response

    async def patch(self, *_args, **_kwargs) -> httpx.Response:
        return self.response

    async def delete(self, *_args, **_kwargs) -> httpx.Response:
        return self.response


@pytest.mark.asyncio
async def test_gateway_proxies_status_code_and_json(client):
    """Ensure gateway surfaces downstream status codes and JSON bodies."""
    original_client = clients.members_client
    fake_response = httpx.Response(201, json={"created": True})
    clients.members_client = _FakeServiceClient(fake_response)

    try:
        response = await client.post("/api/v1/members/test")
    finally:
        clients.members_client = original_client

    assert response.status_code == 201
    assert response.json() == {"created": True}


@pytest.mark.asyncio
async def test_gateway_proxies_non_json_payloads(client):
    """Ensure gateway passes through non-JSON responses (e.g., CSV exports)."""
    original_client = clients.attendance_client
    csv_body = "member_id,name\n1,Test User\n"
    fake_response = httpx.Response(
        200,
        content=csv_body.encode(),
        headers={
            "Content-Type": "text/csv",
            "Content-Disposition": 'attachment; filename="pool-list.csv"',
        },
    )
    clients.attendance_client = _FakeServiceClient(fake_response)

    try:
        response = await client.get("/api/v1/sessions/123/pool-list")
    finally:
        clients.attendance_client = original_client

    assert response.status_code == 200
    assert response.text == csv_body
    assert response.headers["content-type"].startswith("text/csv")
    assert "content-disposition" in response.headers
