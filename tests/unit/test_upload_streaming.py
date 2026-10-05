from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from services.gateway_service.app import clients
from services.media_service.routers import media
from services.media_service.services import storage


@pytest.mark.asyncio
async def test_phone_video_uses_spooled_file_without_buffering(monkeypatch):
    upload = UploadFile(
        BytesIO(b"phone-video"),
        filename="attempt.mov",
        size=11,
        headers=Headers({"content-type": "video/quicktime"}),
    )
    upload.read = AsyncMock(side_effect=AssertionError("Must not buffer a video"))
    stream = AsyncMock(return_value=("https://cdn.test/proof.mov", None))
    monkeypatch.setattr(media.storage_service, "upload_video_file", stream)
    result = await media._upload_file_content(
        upload, "challenge_proof", "proof.mov", storage.BucketType.PUBLIC
    )
    assert result == ("https://cdn.test/proof.mov", None)
    assert stream.await_args.args[0] is upload.file


@pytest.mark.asyncio
async def test_oversized_video_is_rejected_before_storage(monkeypatch):
    upload = UploadFile(
        BytesIO(),
        filename="large.mov",
        size=2 * 1024**3 + 1,
        headers=Headers({"content-type": "video/quicktime"}),
    )
    stream = AsyncMock()
    monkeypatch.setattr(media.storage_service, "upload_video_file", stream)
    with pytest.raises(HTTPException) as error:
        await media._upload_file_content(
            upload, "challenge_proof", "large.mov", storage.BucketType.PUBLIC
        )
    assert error.value.status_code == 413
    stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_s3_video_transfer_runs_in_thread_with_original_content_type(monkeypatch):
    service = storage.StorageService.__new__(storage.StorageService)
    service.backend = "s3"
    service.bucket_public = "test-public"
    service.s3_client = MagicMock()
    thread = AsyncMock()
    monkeypatch.setattr(storage.asyncio, "to_thread", thread)
    video = BytesIO(b"video")
    await service.upload_video_file(
        video, "challenge-proofs/proof.mov", "video/quicktime"
    )
    thread.assert_awaited_once_with(
        service.s3_client.upload_fileobj,
        video,
        "test-public",
        "challenge-proofs/proof.mov",
        ExtraArgs={"ContentType": "video/quicktime"},
    )


@pytest.mark.asyncio
async def test_gateway_never_replays_a_partially_consumed_upload(monkeypatch):
    async def body():
        yield b"video"

    transport = AsyncMock()
    transport.request.side_effect = httpx.ReadTimeout("interrupted")
    monkeypatch.setattr(clients, "_get_shared_client", lambda: transport)
    with pytest.raises(httpx.ReadTimeout):
        await clients.ServiceClient("http://media", timeout=600).post(
            "/uploads", content=body()
        )
    assert transport.request.await_count == 1
