import pytest

from services.sessions_service.services.academy_classification import (
    AcademyParticipationError,
    quote_participation,
)


def test_accelerated_prepaid_uses_one_remaining_class_regardless_of_weekday():
    quote = quote_participation(
        kind="prepaid", purchased_classes=12, already_consumed=4
    )
    assert (quote.fee_kobo, quote.consumes_prepaid_class) == (0, True)


def test_paid_extra_does_not_consume_purchased_class():
    quote = quote_participation(
        kind="paid_extra",
        agreed_fee_kobo=1_500_000,
        purchased_classes=12,
        already_consumed=4,
    )
    assert quote.fee_kobo == 1_500_000
    assert not quote.consumes_prepaid_class


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "prepaid", "purchased_classes": 12, "already_consumed": 12},
        {
            "kind": "paid_extra",
            "agreed_fee_kobo": 0,
            "purchased_classes": 12,
            "already_consumed": 4,
        },
        {
            "kind": "prepaid",
            "agreed_fee_kobo": 1,
            "purchased_classes": 12,
            "already_consumed": 4,
        },
        {"kind": "makeup", "purchased_classes": 12, "already_consumed": 4},
        {"kind": "support", "purchased_classes": 12, "already_consumed": 4},
        {"kind": "prepaid", "purchased_classes": 12, "already_consumed": 13},
    ],
)
def test_rejects_unsafe_or_unfunded_classifications(kwargs):
    with pytest.raises(AcademyParticipationError):
        quote_participation(**kwargs)


def test_makeup_consumes_credit_not_prepaid_class():
    q = quote_participation(
        kind="makeup", purchased_classes=12, already_consumed=4, available_makeups=1
    )
    assert q.consumes_makeup_credit and not q.consumes_prepaid_class


def test_authorized_support_uses_neither_cash_nor_prepaid_balance():
    q = quote_participation(
        kind="support",
        purchased_classes=12,
        already_consumed=12,
        support_authorized=True,
    )
    assert (q.fee_kobo, q.consumes_prepaid_class) == (0, False)


def test_academy_extra_booking_requires_override_reason():
    from pydantic import ValidationError

    from services.sessions_service.routers.academy_participation import (
        AcademyClassAdminBooking,
    )
    from uuid import uuid4

    booking = AcademyClassAdminBooking(
        member_id=uuid4(),
        enrollment_id=uuid4(),
        kind="paid_extra",
        agreed_fee_kobo=1_000_000,
        expected_standard_fee_kobo=1_500_000,
        override_reason="Approved price reduction",
        reason="Extra Saturday class",
    )
    assert booking.expected_standard_fee_kobo == 1_500_000
    assert booking.override_reason == "Approved price reduction"
    with pytest.raises(ValidationError):
        AcademyClassAdminBooking(
            member_id=uuid4(),
            enrollment_id=uuid4(),
            kind="paid_extra",
            agreed_fee_kobo=-1,
            reason="Extra Saturday class",
        )
