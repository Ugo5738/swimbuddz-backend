import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
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
from services.media_service.services.vault_access import require_upload_window
from services.media_service.services.vault_grants import (
    CONTRIBUTOR_REOPEN_DAYS,
    ensure_contributor_window,
    notify_vault_access,
)
from services.media_service.services.vault_templates import (
    DEFAULT_MEDIA_VAULT_CHECKLIST,
)
from services.media_service.tasks.vault_exports import S3MultipartArchiveWriter
from services.media_service.tasks.vault_bandwidth import parse_s3_access_log_line
from services.media_service.tasks.vault_lifecycle import (
    extend_existing_session_vault_window,
    vault_fields_from_session,
)
from services.media_service.tasks.worker import WorkerSettings, task_sync_session_vaults


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


def test_vault_allows_standalone_context_and_rejects_ambiguous_context():
    now = datetime.now(timezone.utc)
    common = {
        "title": "Saturday swim",
        "capture_date": now.date(),
        "upload_opens_at": now,
        "upload_closes_at": now + timedelta(hours=8),
    }
    standalone = VaultCreate(**common)
    assert standalone.session_id is None
    assert standalone.event_id is None
    with pytest.raises(ValidationError):
        VaultCreate(
            **common,
            session_id="00000000-0000-0000-0000-000000000001",
            event_id="00000000-0000-0000-0000-000000000002",
        )


def test_media_labels_are_normalized_and_deduplicated():
    from services.media_service.schemas.vault import ReviewUpdate

    payload = ReviewUpdate(labels=["  Coaching  ", "coaching", "Main   set", ""])

    assert payload.labels == ["Coaching", "Main set"]


def test_media_labels_reject_overly_long_values():
    from services.media_service.schemas.vault import ReviewUpdate

    with pytest.raises(ValidationError, match="40 characters or fewer"):
        ReviewUpdate(labels=["x" * 41])


def test_upload_batch_requires_consent_attestation():
    with pytest.raises(ValidationError):
        UploadBatchCreate(
            expected_files=1,
            expected_bytes=1024,
            consent_attested=False,
            consent_attestation_text="Not accepted",
        )


def test_expired_contributor_assignment_reopens_vault_for_seven_days():
    now = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    vault = SimpleNamespace(
        upload_opens_at=now - timedelta(days=3),
        upload_closes_at=now - timedelta(days=1),
        status="review",
    )

    starts_at, expires_at = ensure_contributor_window(
        vault,
        starts_at=now - timedelta(days=3),
        expires_at=now - timedelta(days=1),
        now=now,
    )

    assert starts_at == now - timedelta(days=3)
    assert expires_at == now + timedelta(days=CONTRIBUTOR_REOPEN_DAYS)
    assert vault.upload_closes_at == expires_at
    assert vault.status == "open"


@pytest.mark.asyncio
async def test_admin_can_bypass_closed_upload_window_but_not_archive():
    now = datetime.now(timezone.utc)
    vault = SimpleNamespace(
        upload_opens_at=now - timedelta(days=4),
        upload_closes_at=now - timedelta(days=1),
        status="review",
    )

    with pytest.raises(HTTPException, match="upload window has closed"):
        await require_upload_window(vault)

    await require_upload_window(vault, bypass_time_window=True)

    vault.status = "archived"
    with pytest.raises(HTTPException, match="vault is archived"):
        await require_upload_window(vault, bypass_time_window=True)


def test_session_vault_defaults_use_local_date_and_coverage_standard():
    now = datetime(2026, 8, 7, 12, tzinfo=timezone.utc)
    fields = vault_fields_from_session(
        {
            "id": "273f49ba-5f04-4d6a-8dcb-e9dc1a1d1b08",
            "title": "Saturday Club Swim",
            "description": "Practice",
            "session_type": "club",
            "starts_at": "2026-08-08T23:30:00+00:00",
            "ends_at": "2026-08-09T02:30:00+00:00",
            "timezone": "Africa/Lagos",
            "location_name": "Rowe Park Pool",
        },
        now=now,
    )

    assert fields["capture_date"].isoformat() == "2026-08-09"
    assert fields["upload_closes_at"] == datetime(
        2026, 8, 12, 2, 30, tzinfo=timezone.utc
    )
    assert fields["auto_transcode"] is False
    assert fields["shot_checklist"] == DEFAULT_MEDIA_VAULT_CHECKLIST
    assert fields["settings_json"]["story"] == [
        "set a goal",
        "prepare",
        "practise",
        "coach",
        "reflect",
        "progress",
        "belong",
    ]


def test_existing_session_vault_is_extended_to_72_hours_after_swim():
    now = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    session = {
        "id": "273f49ba-5f04-4d6a-8dcb-e9dc1a1d1b08",
        "title": "Saturday Club Swim",
        "session_type": "club",
        "starts_at": "2026-08-08T08:00:00+00:00",
        "ends_at": "2026-08-08T12:00:00+00:00",
        "timezone": "Africa/Lagos",
    }
    vault = SimpleNamespace(
        upload_closes_at=datetime(2026, 8, 9, 12, tzinfo=timezone.utc),
        status="review",
    )

    changed = extend_existing_session_vault_window(vault, session, now=now)

    assert changed is True
    assert vault.upload_closes_at == datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
    assert vault.status == "open"


@pytest.mark.asyncio
async def test_assignment_notification_requests_in_app_and_email(monkeypatch):
    from services.media_service.services import vault_grants

    dispatch = AsyncMock(return_value={"dispatched": 1})
    monkeypatch.setattr(vault_grants, "dispatch_notification", dispatch)
    vault = SimpleNamespace(
        id=uuid.UUID("f61fb341-8e27-4134-a6f9-9050ed7ef22e"),
        title="Saturday Club Swim",
    )
    expires_at = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)

    await notify_vault_access(
        vault=vault,
        member_id=uuid.UUID("90d305c8-1ff6-486d-8293-0acc06f49ac0"),
        role="contributor",
        expires_at=expires_at,
    )

    payload = dispatch.await_args.kwargs
    assert payload["channels"] == ["in_app", "email"]
    assert payload["email_template"] == "media_vault_access_granted"
    assert payload["expires_at"] is None
    assert payload["action_url"] == f"/account/media-vault/{vault.id}"
    assert payload["email_data"]["vault_title"] == "Saturday Club Swim"
    assert payload["email_data"]["role_label"] == "media uploader"
    assert (
        payload["email_data"]["responsibility"]
        == "upload full-quality session photos and videos"
    )
    assert payload["email_data"]["expires_at"] == (
        "Monday, 17 August 2026 at 12:00 UTC"
    )
    assert payload["email_data"]["action_url"].endswith(
        f"/account/media-vault/{vault.id}"
    )


def test_media_worker_registers_automatic_session_vault_sync():
    assert task_sync_session_vaults in WorkerSettings.functions


def _s3_log_line(
    *,
    operation: str = "REST.GET.OBJECT",
    object_key: str = "vaults%2F2026-08-02%2Fvault-id%2Foriginals%2Fclip.mov",
    status_code: int = 206,
    bytes_sent: int = 1048576,
    authentication_type: str = "QueryString",
) -> str:
    return (
        "owner private-media [02/Aug/2026:12:34:56 +0000] "
        "203.0.113.7 arn:aws:iam::123456789012:user/media ABC123 "
        f'{operation} {object_key} "GET /private-media/{object_key} HTTP/1.1" '
        f'{status_code} - {bytes_sent} 5368709120 100 90 "-" '
        '"Mozilla/5.0 (iPhone)" - host-id SigV4 TLS_AES_128_GCM_SHA256 '
        f"{authentication_type} private-media.s3.amazonaws.com TLSv1.3 - - us-east-1"
    )


def test_s3_access_log_parser_preserves_actual_range_bytes_and_object_key():
    event = parse_s3_access_log_line(_s3_log_line())

    assert event is not None
    assert event.target_bucket == "private-media"
    assert event.request_id == "ABC123"
    assert event.object_key == "vaults/2026-08-02/vault-id/originals/clip.mov"
    assert event.status_code == 206
    assert event.bytes_sent == 1048576
    assert event.user_agent == "Mozilla/5.0 (iPhone)"
    assert event.authentication_type == "QueryString"


@pytest.mark.parametrize(
    "line",
    [
        _s3_log_line(authentication_type="AuthHeader"),
        _s3_log_line(operation="REST.HEAD.OBJECT"),
        _s3_log_line(object_key="unrelated%2Fclip.mov"),
        _s3_log_line(status_code=403, bytes_sent=243),
        _s3_log_line(bytes_sent=0),
        "not a valid S3 access log line",
    ],
)
def test_s3_access_log_parser_ignores_non_presigned_or_non_download_requests(line):
    assert parse_s3_access_log_line(line) is None
