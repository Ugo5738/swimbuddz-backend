import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import services.payments_service.routers.intents._entitlement._session_bundle as handler


class AsyncClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *args):
        return None


def payment(session_ids: list[str], booking_ids: list[str]):
    return SimpleNamespace(
        id=uuid.uuid4(),
        reference="PAY-BUNDLE",
        member_auth_id="auth-1",
        payer_email=None,
        amount=7000.0,
        currency="NGN",
        payment_metadata={
            "session_ids": session_ids,
            "booking_ids": booking_ids,
            "member_id": str(uuid.uuid4()),
            "session_ride_configs": None,
            "bubbles_to_apply": 0,
        },
    )


@pytest.mark.asyncio
async def test_bundle_fulfilment_confirms_bookings_and_never_writes_attendance(
    monkeypatch,
):
    session_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    booking_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    member_response = httpx.Response(
        200,
        json={"id": str(uuid.uuid4()), "email": None},
        request=httpx.Request("GET", "http://members/member"),
    )
    success = httpx.Response(
        200,
        json={"confirmed": 2, "bookings": []},
        request=httpx.Request("POST", "http://sessions/confirm"),
    )
    client = SimpleNamespace(
        get=AsyncMock(return_value=member_response),
        post=AsyncMock(return_value=success),
    )
    monkeypatch.setattr(
        handler.httpx, "AsyncClient", lambda **kwargs: AsyncClientContext(client)
    )
    monkeypatch.setattr(handler, "_debit_bubbles", AsyncMock(return_value=None))

    await handler.apply_session_bundle(payment(session_ids, booking_ids))

    posted_urls = [str(call.args[0]) for call in client.post.await_args_list]
    assert posted_urls == [
        f"{handler.settings.SESSIONS_SERVICE_URL}/internal/sessions/bookings/bundle/confirm"
    ]
    assert client.post.await_args.kwargs["json"]["booking_ids"] == booking_ids
    assert all("attendance" not in url for url in posted_urls)


@pytest.mark.asyncio
async def test_bundle_fulfilment_fails_for_retry_when_atomic_confirmation_fails(
    monkeypatch,
):
    session_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    booking_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    member_response = httpx.Response(
        200,
        json={"id": str(uuid.uuid4()), "email": None},
        request=httpx.Request("GET", "http://members/member"),
    )
    client = SimpleNamespace(
        get=AsyncMock(return_value=member_response),
        post=AsyncMock(
            return_value=httpx.Response(
                503,
                text="offline",
                request=httpx.Request("POST", "http://sessions/confirm"),
            )
        ),
    )
    monkeypatch.setattr(
        handler.httpx, "AsyncClient", lambda **kwargs: AsyncClientContext(client)
    )
    monkeypatch.setattr(handler, "_debit_bubbles", AsyncMock(return_value=None))

    with pytest.raises(Exception) as exc:
        await handler.apply_session_bundle(payment(session_ids, booking_ids))

    assert getattr(exc.value, "status_code", None) == 502
