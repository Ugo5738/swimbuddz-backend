import httpx
import pytest
from services.gateway_service.app import clients


class _FakeServiceClient:
    """Minimal stand-in for ServiceClient that returns a canned response."""

    def __init__(self, response: httpx.Response):
        self.response = response
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, path: str, *_args, **kwargs) -> httpx.Response:
        self.calls.append(("GET", path, kwargs))
        return self.response

    async def head(self, path: str, *_args, **kwargs) -> httpx.Response:
        self.calls.append(("HEAD", path, kwargs))
        return self.response

    async def post(self, path: str, *_args, **kwargs) -> httpx.Response:
        self.calls.append(("POST", path, kwargs))
        return self.response

    async def put(self, path: str, *_args, **kwargs) -> httpx.Response:
        self.calls.append(("PUT", path, kwargs))
        return self.response

    async def patch(self, path: str, *_args, **kwargs) -> httpx.Response:
        self.calls.append(("PATCH", path, kwargs))
        return self.response

    async def delete(self, path: str, *_args, **kwargs) -> httpx.Response:
        self.calls.append(("DELETE", path, kwargs))
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


MEDIA_ID = "f9f0d723-d888-40de-a953-6f637d194f53"
PRESIGNED_URL = (
    "https://swimbuddz-private.s3.eu-west-1.amazonaws.com/milestone-evidence/x.mp4"
    "?X-Amz-Signature=abc"
)


@pytest.mark.asyncio
async def test_media_playback_relays_redirect_to_browser(client):
    """GET /media/{id}/play must hand the presigned-URL redirect to the
    browser instead of following it (which buffers whole videos in the
    gateway and breaks playback on slow connections)."""
    original_client = clients.media_client
    fake_response = httpx.Response(307, headers={"Location": PRESIGNED_URL})
    fake_client = _FakeServiceClient(fake_response)
    clients.media_client = fake_client

    try:
        response = await client.get(f"/api/v1/media/media/{MEDIA_ID}/play")
    finally:
        clients.media_client = original_client

    assert response.status_code == 307
    assert response.headers["location"] == PRESIGNED_URL
    method, path, kwargs = fake_client.calls[0]
    assert (method, path) == ("GET", f"/media/media/{MEDIA_ID}/play")
    assert kwargs.get("follow_redirects") is False


@pytest.mark.asyncio
async def test_media_playback_forwards_head_probe(client):
    """Browser HEAD probes must reach the media service's HEAD handler
    (the generic proxy 405s on HEAD) and relay its Content-Length."""
    original_client = clients.media_client
    fake_response = httpx.Response(
        200,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Type": "video/mp4",
            "Content-Length": "8624261",
        },
    )
    fake_client = _FakeServiceClient(fake_response)
    clients.media_client = fake_client

    try:
        response = await client.head(f"/api/v1/media/media/{MEDIA_ID}/play")
    finally:
        clients.media_client = original_client

    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["content-length"] == "8624261"
    method, path, _ = fake_client.calls[0]
    assert (method, path) == ("HEAD", f"/media/media/{MEDIA_ID}/play")


@pytest.mark.asyncio
async def test_media_playback_forwards_range_header(client):
    """Range headers drive seek behaviour; they must survive the relay."""
    original_client = clients.media_client
    fake_response = httpx.Response(307, headers={"Location": PRESIGNED_URL})
    fake_client = _FakeServiceClient(fake_response)
    clients.media_client = fake_client

    try:
        response = await client.get(
            f"/api/v1/media/media/{MEDIA_ID}/play",
            headers={"Range": "bytes=1024-"},
        )
    finally:
        clients.media_client = original_client

    assert response.status_code == 307
    _, _, kwargs = fake_client.calls[0]
    assert kwargs["headers"].get("range") == "bytes=1024-"


@pytest.mark.asyncio
async def test_non_playback_media_paths_use_generic_proxy(client):
    """GET /media/{id} (metadata) keeps the buffered JSON proxy behaviour."""
    original_client = clients.media_client
    fake_response = httpx.Response(200, json={"id": MEDIA_ID, "media_type": "video"})
    fake_client = _FakeServiceClient(fake_response)
    clients.media_client = fake_client

    try:
        response = await client.get(f"/api/v1/media/media/{MEDIA_ID}")
    finally:
        clients.media_client = original_client

    assert response.status_code == 200
    assert response.json()["id"] == MEDIA_ID
    method, path, kwargs = fake_client.calls[0]
    assert (method, path) == ("GET", f"/media/media/{MEDIA_ID}")
    # Generic proxy passes no follow_redirects override (defaults to True).
    assert "follow_redirects" not in kwargs


@pytest.mark.asyncio
async def test_head_on_non_playback_media_path_is_rejected(client):
    """HEAD is only supported for playback; other media paths keep 405."""
    original_client = clients.media_client
    fake_client = _FakeServiceClient(httpx.Response(200, json={}))
    clients.media_client = fake_client

    try:
        response = await client.head(f"/api/v1/media/media/{MEDIA_ID}")
    finally:
        clients.media_client = original_client

    assert response.status_code == 405
    assert fake_client.calls == []
