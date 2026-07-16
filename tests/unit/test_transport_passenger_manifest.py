import uuid

import pytest
from pydantic import ValidationError

from services.payments_service.schemas import CreatePaymentIntentRequest
from services.transport_service.models import RidePassengerType
from services.transport_service.routers.bookings import (
    RideBookingCreate,
    _passenger_manifest,
)


def test_legacy_ride_request_builds_member_and_observer_manifest():
    request = RideBookingCreate(
        session_ride_config_id=uuid.uuid4(),
        pickup_location_id=uuid.uuid4(),
        num_seats=3,
    )

    manifest = _passenger_manifest(request)

    assert [passenger.passenger_type for passenger in manifest] == [
        RidePassengerType.MEMBER,
        RidePassengerType.OBSERVER,
        RidePassengerType.OBSERVER,
    ]


def test_transport_rejects_manifest_that_disagrees_with_seat_count():
    with pytest.raises(ValidationError, match="one entry per seat"):
        RideBookingCreate(
            session_ride_config_id=uuid.uuid4(),
            pickup_location_id=uuid.uuid4(),
            num_seats=2,
            passengers=[{"passenger_type": "member"}],
        )


def test_payment_contract_rejects_manifest_that_disagrees_with_seat_count():
    with pytest.raises(ValidationError, match="one entry per seat"):
        CreatePaymentIntentRequest(
            purpose="ride_share",
            session_id=uuid.uuid4(),
            ride_config_id=uuid.uuid4(),
            pickup_location_id=uuid.uuid4(),
            num_seats=2,
            passengers=[{"passenger_type": "observer"}],
        )


def test_payment_contract_rejects_multiple_booking_members():
    with pytest.raises(ValidationError, match="at most one booking member"):
        CreatePaymentIntentRequest(
            purpose="ride_share",
            session_id=uuid.uuid4(),
            ride_config_id=uuid.uuid4(),
            pickup_location_id=uuid.uuid4(),
            num_seats=2,
            passengers=[
                {"passenger_type": "member"},
                {"passenger_type": "member"},
            ],
        )
