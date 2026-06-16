"""Tests for the analyzer transactional emails (services.notify).

Pure helper tests — no DB / no worker. The EmailClient is mocked, so these run
in any env. notify routes through EmailClient.send_template; the branded subject
+ HTML live in communications_service (analyzer_ready / analyzer_failed).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai_service.services import notify

_GET_CLIENT = "services.ai_service.services.notify.get_email_client"


def _client(return_value=True, side_effect=None):
    c = MagicMock()
    c.send_template = AsyncMock(return_value=return_value, side_effect=side_effect)
    return c


@pytest.mark.asyncio
async def test_ready_email_uses_branded_template_with_result_link():
    job_id = uuid.uuid4()
    token = "tok-abc123"
    client = _client()
    with patch(_GET_CLIENT, return_value=client):
        ok = await notify.send_ready_email(job_id, "guest@example.com", token)
    assert ok is True
    client.send_template.assert_awaited_once()
    kwargs = client.send_template.await_args.kwargs
    assert kwargs["template_type"] == "analyzer_ready"
    assert kwargs["to_email"] == "guest@example.com"
    assert (
        kwargs["template_data"]["result_url"]
        == f"{notify.ANALYZER_BASE_URL}/r/{job_id}?guest_token={token}"
    )


@pytest.mark.asyncio
async def test_failed_email_uses_branded_template():
    client = _client()
    with patch(_GET_CLIENT, return_value=client):
        ok = await notify.send_failed_email(uuid.uuid4(), "g@e.com")
    assert ok is True
    kwargs = client.send_template.await_args.kwargs
    assert kwargs["template_type"] == "analyzer_failed"
    assert kwargs["to_email"] == "g@e.com"
    assert kwargs["template_data"]["retry_url"] == notify.ANALYZER_BASE_URL


@pytest.mark.asyncio
async def test_send_is_best_effort_and_never_raises():
    client = _client(side_effect=RuntimeError("comms down"))
    with patch(_GET_CLIENT, return_value=client):
        ok = await notify.send_ready_email(uuid.uuid4(), "g@e.com", "t")
    assert ok is False  # swallowed, not raised
