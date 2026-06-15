"""Integration tests for the PUBLIC (guest) Stroke Lab analyzer — Phase 0.

Covers: guest submit + poll, input validation, the per-job guest_token gate,
the 404-not-403 non-leak (missing/wrong token, member jobs, unknown ids), and
guest-job serialization with a NULL member_auth_id.

Storage + enqueue are mocked so submit runs offline: `mock_public_io` patches
the *sync* Supabase upload (`_upload_sync`) — not `upload_guest_video` — so the
real `make_guest_object_key` still builds the `guest/...` key, and patches the
arq enqueue so no Redis is needed.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.fixture
def mock_public_io():
    """Patch Supabase upload (sync) + arq enqueue so submit runs offline.

    Yields the enqueue mock so tests can assert it was awaited.
    """
    with (
        patch("services.ai_service.analysis.storage._upload_sync", new=MagicMock()),
        patch(
            "services.ai_service.routers.public._enqueue_analysis", new=AsyncMock()
        ) as enq,
    ):
        yield enq


async def _submit(ai_client, email: str = "guest@example.com"):
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
    resp = await ai_client.post(
        _SUBMIT,
        data={"guest_email": "Reddit.User+swim@Gmail.com", "stroke_type": "freestyle"},
        files=_files(),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["stroke_type"] == "freestyle"
    assert body["guest_token"] and len(body["guest_token"]) >= 40
    assert "member_auth_id" not in body  # guest response never leaks it
    job_id = body["job_id"]

    # The persisted row is a public, member-less job keyed to the guest.
    job = await db_session.get(AnalysisJob, uuid.UUID(job_id))
    assert job is not None
    assert job.source == AnalysisJobSource.PUBLIC
    assert job.member_auth_id is None
    assert job.guest_token == body["guest_token"]
    # Phase 0 only lowercases (full canonicalization is Phase 2).
    assert job.guest_email == "reddit.user+swim@gmail.com"
    # The real guest-key builder ran (only the network upload was mocked).
    assert job.video_storage_path == f"guest/{job.guest_token}/{job_id}.mp4"
    # Enqueued exactly once.
    mock_public_io.assert_awaited_once()


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
