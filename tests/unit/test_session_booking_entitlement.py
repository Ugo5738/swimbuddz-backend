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
            "passengers": [
                {"passenger_type": "member", "full_name": "Ada"},
                {"passenger_type": "observer", "full_name": "Tola"},
            ],
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
    assert ride_kwargs["json"]["passengers"][1]["passenger_type"] == "observer"


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


async def test_paid_extra_class_fulfillment_confirms_and_links_payment_without_repricing(
    monkeypatch,
):
    from datetime import timedelta
    from libs.common.datetime_utils import utc_now
    from services.sessions_service.models import SessionBookingStatus, SessionStatus
    from services.sessions_service.routers import internal
    from services.sessions_service.schemas.booking import BookingConfirmRequest

    payment = _payment()
    payment.payment_metadata = {
        key: payment.payment_metadata[key]
        for key in ("booking_id", "session_id", "member_id")
    }
    booking = SimpleNamespace(
        id=uuid.UUID(payment.payment_metadata["booking_id"]),
        session_id=uuid.UUID(payment.payment_metadata["session_id"]),
        status=SessionBookingStatus.PENDING,
        fee_amount_kobo=1500000,
        member_auth_id=payment.member_auth_id,
        payment_intent_id=None,
        wallet_transaction_id=None,
        expires_at=utc_now() + timedelta(minutes=15),
    )
    session = SimpleNamespace(
        id=booking.session_id,
        status=SessionStatus.SCHEDULED,
        starts_at=utc_now() + timedelta(days=1),
        pool_fee=1800000,
        cohort_fee_mode="paid_extra",
    )
    sync = AsyncMock()
    monkeypatch.setattr(internal, "sync_booking_attendance", sync)
    monkeypatch.setattr(
        _session_booking, "_debit_bubbles", AsyncMock(return_value=None)
    )

    class ConfirmingClient(FakeClient):
        async def post(self, url, **kwargs):
            assert url.endswith(f"/internal/sessions/bookings/{booking.id}/confirm")
            db = SimpleNamespace(
                execute=AsyncMock(
                    side_effect=[
                        SimpleNamespace(one_or_none=lambda: booking),
                        SimpleNamespace(scalar_one_or_none=lambda: session),
                        SimpleNamespace(scalar_one=lambda: booking),
                    ]
                ),
                commit=AsyncMock(),
                refresh=AsyncMock(),
            )
            await internal.internal_confirm_booking(
                booking.id,
                BookingConfirmRequest(**kwargs["json"]),
                SimpleNamespace(),
                db,
            )
            return FakeResponse()

    monkeypatch.setattr(
        _session_booking.httpx, "AsyncClient", lambda **kwargs: ConfirmingClient([])
    )
    assert booking.status == SessionBookingStatus.PENDING
    await _session_booking.apply_session_booking(payment)
    assert booking.status == SessionBookingStatus.CONFIRMED
    assert booking.payment_intent_id == payment.id
    assert booking.fee_amount_kobo == 1500000
    assert booking.expires_at is None
    sync.assert_awaited_once_with(booking)
    # Duplicate verification/webhook is idempotent and never creates another booking.
    await _session_booking.apply_session_booking(payment)
    assert booking.payment_intent_id == payment.id
    assert booking.fee_amount_kobo == 1500000
