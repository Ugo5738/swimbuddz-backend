"""Safety invariants for the member obligations read model."""
from types import SimpleNamespace

import pytest
from services.payments_service.models import PaymentPurpose, PaymentStatus
from services.payments_service.routers.intents.obligations import build_overview, classify


TODAY = "2026-10-15"


def enrollment():
    return {
        "id": "enr1", "cohort_name": "Yaba Beginner", "status": "enrolled",
        "payment_status": "paid", "uses_installments": True, "total_installments": 3,
        "installments": [
            {"id": "one", "number": 1, "amount_kobo": 5000000,
             "status": "paid", "due_at": "2026-09-01T12:00:00+01:00"},
            {"id": "two", "number": 2, "amount_kobo": 5000000,
             "status": "pending", "due_at": "2026-10-19T12:00:00+01:00"},
            {"id": "three", "number": 3, "amount_kobo": 4500000,
             "status": "pending", "due_at": "2026-11-19T12:00:00+01:00"},
        ],
    }


def test_future_installments_not_marked_overdue():
    response = build_overview([enrollment()], [], [], [], [], TODAY)
    assert response["due_kobo"] == 0
    assert response["upcoming_kobo"] == 9500000
    assert response["complete"] is True


def test_confirmed_session_counts_only_once_and_store_not_debt():
    response = build_overview(
        [], [{"id": "booking", "name": "Swim", "amount_kobo": 700000}],
        [{"id": "order", "order_number": "S-01", "title": "Store order",
          "amount_kobo": 600000, "status": "pending_payment"}],
        [], [], TODAY,
    )
    assert response["due_kobo"] == 700000
    assert len(response["optional_purchases"]) == 1


def test_manual_transfer_matching_keeps_amount_out_of_pay_now():
    payment = SimpleNamespace(
        purpose=PaymentPurpose.ACADEMY_COHORT,
        status=PaymentStatus.PENDING_REVIEW,
        payment_method="manual_transfer",
        payment_metadata={"installment_id": "two"},
        session_booking_id=None,
    )
    entry = enrollment()
    entry["installments"][1]["status"] = "missed"
    response = build_overview([entry], [], [], [payment], [], TODAY)
    assert response["due_kobo"] == 0
    assert response["checking_kobo"] == 5000000


def test_failed_sources_and_missing_schedule_cannot_claim_complete():
    entry = enrollment()
    entry["installments"] = []
    response = build_overview([entry], None, None, [], ["sessions", "store"], TODAY)
    assert response["complete"] is False
    assert response["unpriced"][0]["id"] == entry["id"]
    assert response["sources_unavailable"] == ["sessions", "store"]


def test_closed_or_waived_not_counted():
    entry = enrollment()
    entry["status"] = "dropped"
    assert build_overview([entry], [], [], [], [], TODAY)["items"] == []
    entry["status"] = "enrolled"
    for row in entry["installments"]:
        row["status"] = "waived"
    assert build_overview([entry], [], [], [], [], TODAY)["due_kobo"] == 0


@pytest.mark.parametrize("date,expected", [
    ("2026-10-14T23:20:00Z", "due"),
    ("2026-10-19T10:00:00+01:00", "upcoming"),
    ("2026-10-12T10:00:00+01:00", "overdue"),
])
def test_lagos_calendar_day(date, expected):
    assert classify(date, "pending", TODAY) == expected
