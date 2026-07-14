import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.payments_service.routers.intents._entitlement import _session_booking


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.responses.pop(0)


def _payment():
    session_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        reference="PAY-SESSION-1",
        member_auth_id="member-auth-1",
        payment_metadata={
            "booking_id": str(uuid.uuid4()),
            "session_id": str(session_id),
            "member_id": str(uuid.uuid4()),
            "ride_config_id": str(uuid.uuid4()),
            "pickup_location_id": str(uuid.uuid4()),
            "num_seats": 2,
        },
    )


@pytest.mark.asyncio
async def test_session_booking_confirms_owner_and_fulfills_quoted_ride(monkeypatch):
    payment = _payment()
    client = FakeClient([FakeResponse(), FakeResponse()])
    monkeypatch.setattr(
        _session_booking.httpx,
        "AsyncClient",
        lambda **kwargs: client,
    )
    monkeypatch.setattr(
        _session_booking,
        "_debit_bubbles",
        AsyncMock(return_value=str(uuid.uuid4())),
    )

    await _session_booking.apply_session_booking(payment)

    confirm_url, confirm_kwargs = client.posts[0]
    assert confirm_url.endswith(
        f"/internal/sessions/bookings/{payment.payment_metadata['booking_id']}/confirm"
    )
    assert confirm_kwargs["json"]["member_auth_id"] == payment.member_auth_id
    ride_url, ride_kwargs = client.posts[1]
    assert ride_url.endswith(
        f"/transport/sessions/{payment.payment_metadata['session_id']}/bookings"
    )
    assert ride_kwargs["json"]["num_seats"] == 2


@pytest.mark.asyncio
async def test_session_booking_ride_failure_keeps_fulfillment_retryable(monkeypatch):
    payment = _payment()
    client = FakeClient([FakeResponse(), FakeResponse(503, "transport down")])
    monkeypatch.setattr(
        _session_booking.httpx,
        "AsyncClient",
        lambda **kwargs: client,
    )
    monkeypatch.setattr(
        _session_booking,
        "_debit_bubbles",
        AsyncMock(return_value=None),
    )

    with pytest.raises(Exception) as exc:
        await _session_booking.apply_session_booking(payment)

    assert getattr(exc.value, "status_code", None) == 502
