"""The Stroke Lab storage adapter must route through S3 when STORAGE_BACKEND=s3,
mapping the logical bucket (strokelab-uploads / strokelab-annotated) to a key prefix
inside one private S3 bucket — so it joins the rest of the app on S3 and the jpeg
evidence frames (rejected by Supabase's video-only bucket) just work. No real AWS."""

from __future__ import annotations

import services.ai_service.analysis.storage as st


class _FakeS3:
    def __init__(self, log: dict):
        self._log = log

    def put_object(self, **kw):
        self._log["put"] = kw

    def generate_presigned_url(self, op, **kw):
        self._log["presign"] = {"op": op, **kw}
        return "https://swimbuddz-private.s3/signed"

    def delete_object(self, **kw):
        self._log["delete"] = kw


def _wire_s3(monkeypatch):
    log: dict = {}
    monkeypatch.setattr(st, "_use_s3", lambda: True)
    monkeypatch.setattr(st, "_s3_bucket", lambda: "swimbuddz-private")
    monkeypatch.setattr(st, "_s3_client", lambda: _FakeS3(log))
    return log


def test_upload_routes_to_s3_with_prefixed_key(monkeypatch):
    log = _wire_s3(monkeypatch)
    st._upload_sync(
        "strokelab-annotated", "guest/tok/job/evidence/x.jpg", b"...", "image/jpeg"
    )
    assert log["put"]["Bucket"] == "swimbuddz-private"
    # logical bucket becomes the key prefix in the single private bucket
    assert log["put"]["Key"] == "strokelab-annotated/guest/tok/job/evidence/x.jpg"
    assert log["put"]["ContentType"] == "image/jpeg"  # S3 has no MIME allow-list


def test_signed_url_uses_presign_on_the_prefixed_key(monkeypatch):
    log = _wire_s3(monkeypatch)
    url = st._signed_url_sync("strokelab-uploads", "guest/tok/job.mp4", 3600)
    assert url == "https://swimbuddz-private.s3/signed"
    assert log["presign"]["op"] == "get_object"
    assert log["presign"]["Params"] == {
        "Bucket": "swimbuddz-private",
        "Key": "strokelab-uploads/guest/tok/job.mp4",
    }
    assert log["presign"]["ExpiresIn"] == 3600


def test_supabase_path_unchanged_when_not_s3(monkeypatch):
    monkeypatch.setattr(st, "_use_s3", lambda: False)
    seen: dict = {}

    class _FakeBucket:
        def upload(self, key, data, file_options):
            seen.update(key=key, opts=file_options)

    class _FakeStorage:
        def from_(self, bucket):
            seen["bucket"] = bucket
            return _FakeBucket()

    class _FakeClient:
        storage = _FakeStorage()

    monkeypatch.setattr(st, "get_supabase_admin_client", lambda: _FakeClient())
    st._upload_sync("strokelab-uploads", "k.mp4", b"x", "video/mp4")
    # supabase path: the bucket is the bucket (no prefixing), unchanged behaviour
    assert seen["bucket"] == "strokelab-uploads" and seen["key"] == "k.mp4"
