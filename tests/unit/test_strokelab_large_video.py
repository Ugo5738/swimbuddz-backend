"""Safety regressions for large high-quality S3 StrokeLab uploads."""

import subprocess
from unittest.mock import AsyncMock

import pytest

from services.ai_service.analysis import storage


@pytest.mark.asyncio
async def test_large_public_video_uses_remote_window_not_full_download(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        storage,
        "_signed_url_for_path",
        AsyncMock(return_value="https://s3.example.test/clip"),
    )
    monkeypatch.setattr(
        storage,
        "verify_media_object",
        AsyncMock(return_value={"size_bytes": 300 * 1024 * 1024}),
    )
    copied = []

    async def fake_to_thread(fn, *args):
        copied.append((fn, args))

    monkeypatch.setattr(storage.asyncio, "to_thread", fake_to_thread)
    path = await storage.download_storage_path(
        "media:strokelab/original/clip.mov",
        tmp_path,
        public_window_seconds=120,
    )
    assert path.name.endswith("_window.mp4")
    assert len(copied) == 1
    assert copied[0][0] == storage._transcode_remote_clip
    assert copied[0][1][2] == 120


def test_remote_transcode_does_not_expose_signed_url_on_failure(monkeypatch, tmp_path):
    secret_url = "https://s3.example.test/clip?X-Amz-Signature=PRIVATE_SECRET"

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, [secret_url])

    monkeypatch.setattr(storage.subprocess, "run", fail)
    with pytest.raises(RuntimeError) as raised:
        storage._transcode_remote_clip(secret_url, tmp_path / "clip.mp4", 120)
    assert "PRIVATE_SECRET" not in str(raised.value)
    assert not (tmp_path / "clip.mp4").exists()
