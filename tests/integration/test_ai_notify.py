"""Tests for the analyzer transactional emails (services.notify).

Pure helper tests — no DB / no worker. The EmailClient is mocked, so these run
in any env.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai_service.services import notify

_GET_CLIENT = "services.ai_service.services.notify.get_email_client"


def _client(send_return=True, send_side_effect=None):
    c = MagicMock()
    c.send = AsyncMock(return_value=send_return, side_effect=send_side_effect)
    return c


@pytest.mark.asyncio
async def test_ready_email_carries_the_result_link():
    job_id = uuid.uuid4()
    token = "tok-abc123"
    client = _client()
    with patch(_GET_CLIENT, return_value=client):
        ok = await notify.send_ready_email(job_id, "guest@example.com", token)
    assert ok is True
    client.send.assert_awaited_once()
    kwargs = client.send.await_args.kwargs
    assert kwargs["to_email"] == "guest@example.com"
    assert "ready" in kwargs["subject"].lower()
    link = f"/r/{job_id}?guest_token={token}"
    assert link in kwargs["body"]
    assert link in kwargs["html_body"]


@pytest.mark.asyncio
async def test_failed_email_mentions_the_refund():
    client = _client()
    with patch(_GET_CLIENT, return_value=client):
        ok = await notify.send_failed_email(uuid.uuid4(), "g@e.com")
    assert ok is True
    assert "refund" in client.send.await_args.kwargs["body"].lower()


@pytest.mark.asyncio
async def test_send_is_best_effort_and_never_raises():
    client = _client(send_side_effect=RuntimeError("comms down"))
    with patch(_GET_CLIENT, return_value=client):
        ok = await notify.send_ready_email(uuid.uuid4(), "g@e.com", "t")
    assert ok is False  # swallowed, not raised
