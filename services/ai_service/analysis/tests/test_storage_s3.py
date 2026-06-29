"""Stroke Lab storage goes through media_service, including legacy S3 keys."""

from __future__ import annotations

import uuid

import pytest

import services.ai_service.analysis.storage as st


pytestmark = pytest.mark.asyncio


async def test_signed_url_for_legacy_upload_uses_media_service_prefixed_key(
    monkeypatch,
):
    seen: dict = {}

    async def fake_sign_media_object(**kwargs):
        seen.update(kwargs)
        return "https://media/signed"

    monkeypatch.setattr(st, "sign_media_object", fake_sign_media_object)

    url = await st.signed_url_for_upload("guest/tok/job.mp4", expires_in=123)

    assert url == "https://media/signed"
    assert seen == {
        "object_key": "strokelab-uploads/guest/tok/job.mp4",
        "calling_service": "ai_service",
        "expires_in": 123,
    }


async def test_signed_url_for_media_reference_keeps_object_key(monkeypatch):
    seen: dict = {}

    async def fake_sign_media_object(**kwargs):
        seen.update(kwargs)
        return "https://media/signed"

    monkeypatch.setattr(st, "sign_media_object", fake_sign_media_object)

    await st.signed_url_for_evidence("media:strokelab/evidence/x.jpg")

    assert seen["object_key"] == "strokelab/evidence/x.jpg"
    assert seen["calling_service"] == "ai_service"


async def test_delete_job_assets_uses_media_service_for_legacy_and_media_keys(
    monkeypatch,
):
    seen: list[dict] = []

    async def fake_delete_media_object(**kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(st, "delete_media_object", fake_delete_media_object)

    await st.delete_job_assets(
        "guest/tok/job.mp4",
        "guest/tok/job-annotated.mp4",
        ["media:strokelab/evidence/x.jpg", "guest/tok/evidence/y.jpg"],
    )

    assert [item["object_key"] for item in seen] == [
        "strokelab-uploads/guest/tok/job.mp4",
        "strokelab-annotated/guest/tok/job-annotated.mp4",
        "strokelab/evidence/x.jpg",
        "strokelab-annotated/guest/tok/evidence/y.jpg",
    ]
    assert {item["calling_service"] for item in seen} == {"ai_service"}


async def test_upload_evidence_frames_returns_media_storage_references(monkeypatch):
    job_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    seen: list[dict] = []

    async def fake_upload_media_object(**kwargs):
        seen.append(kwargs)
        return {"object_key": f"strokelab/evidence/{kwargs['filename']}"}

    monkeypatch.setattr(st, "upload_media_object", fake_upload_media_object)

    result = await st.upload_evidence_frames(
        "guest/tok",
        job_id,
        {"holistic_coach:3": b"jpeg"},
    )

    assert result == {
        "holistic_coach:3": "media:strokelab/evidence/holistic_coach_3.jpg"
    }
    assert seen[0]["purpose"] == "strokelab_evidence"
    assert seen[0]["content_type"] == "image/jpeg"
    assert seen[0]["linked_id"] == f"guest/tok/{job_id}/evidence"
    assert seen[0]["calling_service"] == "ai_service"
