"""Unit tests for payment-triggered referral qualification helper."""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from services.payments_service.routers.intents import _try_qualify_referral


@pytest.mark.asyncio
@pytest.mark.unit
async def test_try_qualify_referral_posts_expected_payload():
    """Referral qualification call sends JSON payload and logs success."""
    response = httpx.Response(
        status_code=200,
        json={"success": True, "qualified": True},
        request=httpx.Request("POST", "http://wallet/internal/wallet/referral-qualify"),
    )

    with (
        patch(
            "services.payments_service.routers.intents.internal_post",
            new=AsyncMock(return_value=response),
        ) as internal_post_mock,
        patch(
            "services.payments_service.routers.intents.logger.info", new=Mock()
        ) as info_mock,
    ):
        await _try_qualify_referral("auth-user-1", "PAY-123")

    internal_post_mock.assert_awaited_once()
    kwargs = internal_post_mock.await_args.kwargs
    assert kwargs["path"] == "/internal/wallet/referral-qualify"
    assert kwargs["calling_service"] == "payments"
    assert kwargs["json"] == {
        "member_auth_id": "auth-user-1",
        "trigger": "membership_payment:PAY-123",
    }
    info_mock.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_try_qualify_referral_handles_http_failure_without_raising():
    """HTTP failures are logged and never break payment fulfillment."""
    response = httpx.Response(
        status_code=500,
        text="wallet error",
        request=httpx.Request("POST", "http://wallet/internal/wallet/referral-qualify"),
    )

    with (
        patch(
            "services.payments_service.routers.intents.internal_post",
            new=AsyncMock(return_value=response),
        ) as internal_post_mock,
        patch(
            "services.payments_service.routers.intents.logger.warning", new=Mock()
        ) as warning_mock,
    ):
        await _try_qualify_referral("auth-user-2", "PAY-456")

    internal_post_mock.assert_awaited_once()
    warning_mock.assert_called()
