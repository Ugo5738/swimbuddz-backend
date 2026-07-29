import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from services.media_service.routers.vaults import _media_type_for, _safe_filename
from services.media_service.schemas.vault import (
    MultipartInitiateRequest,
    UploadBatchCreate,
    VaultCreate,
)
from services.media_service.services.storage import (
    attachment_content_disposition,
    recommended_multipart_part_size,
)
from services.media_service.tasks.vault_exports import S3MultipartArchiveWriter


class FakeMultipartS3:
    def __init__(self):
        self.uploads: list[bytes] = []
        self.completed = False
        self.aborted = False

    def create_multipart_upload(self, **_kwargs):
        return {"UploadId": "test-upload"}

    def upload_part(self, **kwargs):
        self.uploads.append(bytes(kwargs["Body"]))
        return {"ETag": f'"part-{len(self.uploads)}"'}

    def complete_multipart_upload(self, **_kwargs):
        self.completed = True

    def abort_multipart_upload(self, **_kwargs):
        self.aborted = True


def test_multipart_size_supports_500_gib_within_s3_part_limit():
    size_bytes = 500 * 1024**3
    part_size = recommended_multipart_part_size(size_bytes)

    assert part_size >= 5 * 1024**2
    assert (size_bytes + part_size - 1) // part_size <= 9990
    assert part_size % (1024**2) == 0


def test_small_upload_uses_network_resilient_16_mib_parts():
    assert recommended_multipart_part_size(100 * 1024**2) == 16 * 1024**2


def test_upload_rejects_objects_above_s3_five_tib_limit():
    with pytest.raises(ValidationError):
        MultipartInitiateRequest(
            filename="too-large.mov",
            content_type="video/quicktime",
            size_bytes=5 * 1024**4 + 1,
        )


def test_download_filename_cannot_inject_response_headers():
    header = attachment_content_disposition("session\r\nunsafe.mov")

    assert "\r" not in header
    assert "\n" not in header
    assert header.endswith("sessionunsafe.mov")


def test_zip_export_streams_as_s3_multipart_without_seekable_disk():
    client = FakeMultipartS3()
    writer = S3MultipartArchiveWriter(
        client=client,
        bucket="private",
        key="export.zip",
        download_name="session.zip",
        part_size=64,
    )
    with zipfile.ZipFile(writer, mode="w", allowZip64=True) as archive:
        archive.writestr("clip.txt", b"original-media" * 100)
    size = writer.complete()

    archive_bytes = b"".join(client.uploads)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert archive.read("clip.txt") == b"original-media" * 100
    assert size == len(archive_bytes)
    assert len(client.uploads) > 1
    assert client.completed is True
    assert client.aborted is False


def test_phone_formats_are_accepted_without_conversion():
    assert _media_type_for("image/heic", "IMG_1001.HEIC").value == "IMAGE"
    assert _media_type_for("video/quicktime", "IMG_1002.MOV").value == "VIDEO"
    assert _media_type_for("application/octet-stream", "IMG_1003.MOV").value == "VIDEO"


def test_filename_cannot_escape_vault_prefix():
    assert _safe_filename("../../private/IMG 1001.MOV") == "IMG 1001.MOV"
    assert _safe_filename("pool/../clip<>.mov") == "clip_.mov"


def test_vault_requires_one_context_and_valid_upload_window():
    now = datetime.now(timezone.utc)
    common = {
        "title": "Saturday swim",
        "capture_date": now.date(),
        "upload_opens_at": now,
        "upload_closes_at": now + timedelta(hours=8),
    }
    with pytest.raises(ValidationError):
        VaultCreate(**common)
    with pytest.raises(ValidationError):
        VaultCreate(
            **common,
            session_id="00000000-0000-0000-0000-000000000001",
            event_id="00000000-0000-0000-0000-000000000002",
        )


def test_upload_batch_requires_consent_attestation():
    with pytest.raises(ValidationError):
        UploadBatchCreate(
            expected_files=1,
            expected_bytes=1024,
            consent_attested=False,
            consent_attestation_text="Not accepted",
        )
