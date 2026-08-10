from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from services.events_service.services.pricing import (
    event_pricing_response,
    normalize_event_pricing,
)
from services.events_service.tasks.reminders import due_reminder_offsets


def test_cost_plus_pricing_uses_activity_quote_lines_and_margin():
    result = normalize_event_pricing(
        {
            "pricing_mode": "cost_plus",
            "pricing_expected_attendees": 10,
            "cost_lines": [
                {
                    "category": "pool",
                    "description": "Pool access",
                    "charge_basis": "per_attendee",
                    "unit_cost_naira": 3000,
                    "quantity": 10,
                },
                {
                    "category": "refreshment",
                    "description": "Light refreshment",
                    "charge_basis": "per_attendee",
                    "unit_cost_naira": 1000,
                    "quantity": 10,
                },
            ],
            "margin_type": "fixed_per_attendee",
            "margin_value": 3000,
        }
    )

    assert result["estimated_total_cost"] == 4_000_000
    assert result["estimated_cost_per_attendee"] == 400_000
    assert result["margin_amount_per_attendee"] == 300_000
    assert result["cost_kobo"] == 700_000


def test_included_pricing_never_creates_an_attendee_charge():
    result = normalize_event_pricing(
        {
            "pricing_mode": "included",
            "cost_naira": 7000,
            "pricing_expected_attendees": 20,
        }
    )

    assert result["cost_kobo"] is None


def test_pricing_response_converts_snapshot_back_to_naira():
    event = SimpleNamespace(
        cost_kobo=700_000,
        pricing_mode="cost_plus",
        pricing_expected_attendees=10,
        max_capacity=20,
        cost_lines=[
            {
                "category": "pool",
                "description": "Pool access",
                "charge_basis": "per_attendee",
                "unit_cost_kobo": 300_000,
                "quantity": 10,
                "source_rate_type": None,
                "source_rate_id": None,
            }
        ],
        estimated_total_cost=3_000_000,
        estimated_cost_per_attendee=300_000,
        margin_type="percentage",
        margin_value=2000,
        margin_amount_per_attendee=60_000,
    )

    response = event_pricing_response(event)

    assert response["cost_naira"] == 7000
    assert response["margin_value"] == 20
    assert response["estimated_cost_per_attendee_naira"] == 3000


def test_due_offsets_only_include_current_delivery_window():
    now = datetime(2027, 1, 7, 18, 0, tzinfo=timezone.utc)
    event = SimpleNamespace(
        start_time=now + timedelta(hours=24),
        email_reminder_hours=[168, 24, 1],
    )

    assert due_reminder_offsets(event, now) == [24]
