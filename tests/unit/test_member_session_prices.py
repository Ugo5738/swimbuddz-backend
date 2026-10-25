from unittest.mock import AsyncMock

import httpx
import pytest

from services.communications_service.services import member_session_prices as prices
from services.communications_service.services.session_email_context import (
    with_booking_state,
)


@pytest.mark.asyncio
async def test_prices_are_batched_deduplicated_and_keyed_by_person_and_swim(
    monkeypatch,
):
    call = AsyncMock(
        return_value=httpx.Response(
            200,
            request=httpx.Request("POST", "http://sessions/member-prices"),
            json={
                "items": [
                    {"context_key": "swim:ay", "fee_amount_kobo": 1_000_000},
                    {"context_key": "swim:normal", "fee_amount_kobo": 1_500_000},
                ]
            },
        )
    )
    monkeypatch.setattr(prices, "internal_post", call)
    result = await prices.load_member_session_prices(
        [{"id": "swim", "session_type": "club"}],
        [{"id": "ay"}, {"id": "normal"}, {"id": "ay"}],
    )
    assert result["swim"]["ay"]["fee_amount_kobo"] == 1_000_000
    assert result["swim"]["normal"]["fee_amount_kobo"] == 1_500_000
    assert len(call.await_args.kwargs["json"]["checks"]) == 2


@pytest.mark.parametrize(
    "fee,expected",
    [
        (1_000_000, "NGN 10,000"),
        (1_500_000, "NGN 15,000"),
        (0, "No additional pool fee"),
    ],
)
def test_member_context_does_not_mutate_another_recipients_fee(fee, expected):
    shared = {"session_type": "club", "pool_fee": 15000, "fee_text": "NGN 15,000"}
    own = with_booking_state(
        shared, is_booked=False, member_price={"fee_amount_kobo": fee}
    )
    assert own["fee_text"] == expected
    assert own["pool_fee"] == fee / 100
    assert shared["pool_fee"] == 15000


@pytest.mark.asyncio
async def test_unverified_member_fee_is_not_replaced_with_public_price(monkeypatch):
    monkeypatch.setattr(
        prices,
        "internal_post",
        AsyncMock(side_effect=httpx.ConnectError("Unavailable")),
    )
    result = await prices.load_member_session_prices(
        [{"id": "swim", "session_type": "club"}],
        [{"id": "ay"}],
    )
    assert result == {}
    context = with_booking_state(
        {"session_type": "club", "pool_fee": 15000, "fee_text": "NGN 15,000"},
        is_booked=False,
    )
    assert context["pool_fee"] is None
    assert "15,000" not in context["fee_text"]


@pytest.mark.asyncio
async def test_non_club_email_context_does_not_request_club_prices(monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(prices, "internal_post", call)
    await prices.load_member_session_prices(
        [{"id": "swim", "session_type": "community"}], [{"id": "ay"}]
    )
    call.assert_not_awaited()
