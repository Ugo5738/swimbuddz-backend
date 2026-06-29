"""Integration tests for the PUBLIC (guest) Stroke Lab analyzer.

Covers: guest submit + poll, input validation, the per-job guest_token gate,
the 404-not-403 non-leak (missing/wrong token, member jobs, unknown ids),
guest-job serialization with a NULL member_auth_id, and the credit gate
(first submit per email is free; the next is paywalled with 402).

Storage + enqueue are mocked so submit runs offline: `mock_public_io` patches
the media-service client boundary used by `upload_guest_video`, and patches the
arq enqueue so no Redis is needed. The credit ledger runs for real against the
test db_session. Emails are unique per call so each test gets its own fresh free
credit.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from services.ai_service.models import (
    AnalysisJob,
    AnalysisJobSource,
    AnalysisJobStatus,
)

_SUBMIT = "/ai/public/analyze"


def _poll(job_id: str) -> str:
    return f"/ai/public/analyze/{job_id}"


def _files(content: bytes = b"\x00\x01\x02fake-mp4-bytes", name: str = "clip.mp4"):
    return {"video": (name, content, "video/mp4")}


def _unique_email() -> str:
    return f"guest-{uuid.uuid4().hex}@example.com"


@pytest.fixture
def mock_public_io():
    """Patch media upload/delete + arq enqueue so submit runs offline.

    Yields the enqueue mock so tests can assert it was awaited.
    """

    async def _fake_upload_media_object(**kwargs):
        return {
            "object_key": (
                "strokelab/original/" f"{kwargs['linked_id']}/{kwargs['filename']}"
            )
        }

    with (
        patch(
            "services.ai_service.analysis.storage.upload_media_object",
            new=AsyncMock(side_effect=_fake_upload_media_object),
        ),
        patch(
            "services.ai_service.analysis.storage.delete_media_object",
            new=AsyncMock(),
        ),
        patch(
            "services.ai_service.routers.public._enqueue_analysis", new=AsyncMock()
        ) as enq,
    ):
        yield enq


async def _submit(ai_client, email: str = ""):
    email = email or _unique_email()
    resp = await ai_client.post(
        _SUBMIT,
        data={"guest_email": email, "stroke_type": "freestyle"},
        files=_files(),
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


# ── submit ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_submit_creates_guest_job(ai_client, db_session, mock_public_io):
    email = f"Reddit.User+swim-{uuid.uuid4().hex[:8]}@Gmail.com"
    resp = await ai_client.post(
        _SUBMIT,
        data={"guest_email": email, "stroke_type": "freestyle"},
        files=_files(),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["stroke_type"] == "freestyle"
    assert body["guest_token"] and len(body["guest_token"]) >= 40
    assert body["credits_remaining"] == 0  # the free credit was just reserved
    assert "member_auth_id" not in body  # guest response never leaks it
    job_id = body["job_id"]

    job = await db_session.get(AnalysisJob, uuid.UUID(job_id))
    assert job is not None
    assert job.source == AnalysisJobSource.PUBLIC
    assert job.member_auth_id is None
    assert job.guest_token == body["guest_token"]
    # The job stores the typed (lowercased) email; the credit account is keyed to
    # the canonical form (+tag / Gmail-dots stripped).
    assert job.guest_email == email.lower()
    # The job stores an opaque media-service object reference.
    assert job.video_storage_path == (
        "media:strokelab/original/" f"guest/{job.guest_token}/{job_id}/{job_id}.mp4"
    )
    mock_public_io.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_submit_second_submit_same_email_paywalled(
    ai_client, mock_public_io
):
    email = f"once-{uuid.uuid4().hex}@example.com"
    r1 = await ai_client.post(
        _SUBMIT, data={"guest_email": email, "stroke_type": "freestyle"}, files=_files()
    )
    assert r1.status_code == 202, r1.text  # free analysis
    assert r1.json()["credits_remaining"] == 0
    r2 = await ai_client.post(
        _SUBMIT, data={"guest_email": email, "stroke_type": "freestyle"}, files=_files()
    )
    assert r2.status_code == 402, r2.text  # free used, no purchased credits
    assert r2.json()["detail"]["reason"] == "no_credits"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_submit_rejects_non_freestyle(ai_client, mock_public_io):
    resp = await ai_client.post(
        _SUBMIT,
        data={"guest_email": "a@b.com", "stroke_type": "butterfly"},
        files=_files(),
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_submit_rejects_bad_email(ai_client, mock_public_io):
    resp = await ai_client.post(
        _SUBMIT,
        data={"guest_email": "not-an-email", "stroke_type": "freestyle"},
        files=_files(),
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_submit_rejects_empty_video(ai_client, mock_public_io):
    resp = await ai_client.post(
        _SUBMIT,
        data={"guest_email": "a@b.com", "stroke_type": "freestyle"},
        files=_files(content=b""),
    )
    assert resp.status_code == 400, resp.text


# ── poll + token gate (404 non-leak) ─────────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_poll_with_token_header_and_query(ai_client, mock_public_io):
    job = await _submit(ai_client)
    jid, token = job["job_id"], job["guest_token"]

    # Header form (preferred — keeps the token out of URLs/logs).
    r1 = await ai_client.get(_poll(jid), headers={"X-Guest-Token": token})
    assert r1.status_code == 200, r1.text
    detail = r1.json()
    assert detail["job_id"] == jid
    assert detail["status"] == "pending"
    assert detail["result"] is None
    assert "member_auth_id" not in detail  # guest detail never leaks it

    # Query form (in-session FE fallback).
    r2 = await ai_client.get(_poll(jid), params={"guest_token": token})
    assert r2.status_code == 200, r2.text
    assert r2.json()["job_id"] == jid


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_poll_404_without_token(ai_client, mock_public_io):
    job = await _submit(ai_client)
    r = await ai_client.get(_poll(job["job_id"]))
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_poll_404_wrong_token(ai_client, mock_public_io):
    job = await _submit(ai_client)
    r = await ai_client.get(
        _poll(job["job_id"]), headers={"X-Guest-Token": "wrong-token"}
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_poll_404_unknown_job(ai_client):
    r = await ai_client.get(_poll(str(uuid.uuid4())), headers={"X-Guest-Token": "x"})
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_poll_404_for_member_job(ai_client, db_session):
    """A member job must never be readable via the public endpoint, even with a
    token guess — source != public short-circuits to 404 (existence non-leak)."""
    mj = AnalysisJob(
        member_auth_id=uuid.uuid4(),
        stroke_type="freestyle",
        video_storage_path="member/x.mp4",
        status=AnalysisJobStatus.PENDING,
        source=AnalysisJobSource.MEMBER,
    )
    db_session.add(mj)
    await db_session.commit()

    r = await ai_client.get(_poll(str(mj.id)), headers={"X-Guest-Token": "anything"})
    assert r.status_code == 404, r.text
