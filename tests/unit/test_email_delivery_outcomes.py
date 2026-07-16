import httpx
import pytest

from libs.common.emails import core
from libs.common.emails.core import EmailDeliveryUnknownError


class FailingAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, *args, **kwargs):
        request = httpx.Request("POST", core.BREVO_API_URL)
        raise httpx.ReadTimeout("provider timed out", request=request)


@pytest.mark.asyncio
async def test_brevo_timeout_can_be_reported_as_unknown(monkeypatch):
    monkeypatch.setattr(core.httpx, "AsyncClient", FailingAsyncClient)

    with pytest.raises(EmailDeliveryUnknownError):
        await core._send_via_brevo_api(
            "api-key",
            "member@example.com",
            "Subject",
            "Body",
            None,
            "hello@swimbuddz.com",
            "SwimBuddz",
            raise_on_unknown=True,
        )


@pytest.mark.asyncio
async def test_legacy_email_callers_still_receive_false_on_timeout(monkeypatch):
    monkeypatch.setattr(core.httpx, "AsyncClient", FailingAsyncClient)

    success = await core._send_via_brevo_api(
        "api-key",
        "member@example.com",
        "Subject",
        "Body",
        None,
        "hello@swimbuddz.com",
        "SwimBuddz",
    )

    assert success is False
