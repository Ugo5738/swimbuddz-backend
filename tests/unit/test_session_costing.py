"""Unit coverage for editable session cost snapshots and margins."""

from services.sessions_service.services.pricing import normalize_pricing_payload


def _base_payload(**overrides):
    payload = {
        "capacity": 20,
        "pricing_expected_attendees": 20,
        "pricing_mode": "cost_plus",
        "cost_lines": [
            {
                "category": "pool",
                "description": "Pool admission",
                "charge_basis": "per_attendee",
                "unit_cost_naira": 3000,
                "quantity": 20,
            },
            {
                "category": "refreshment",
                "description": "Light refreshment",
                "charge_basis": "per_attendee",
                "unit_cost_naira": 1000,
                "quantity": 20,
            },
        ],
        "margin_type": "fixed_per_attendee",
        "margin_value": 1000,
    }
    payload.update(overrides)
    return payload


def test_cost_plus_fixed_margin_sets_booking_price():
    result = normalize_pricing_payload(_base_payload())

    assert result["estimated_total_cost"] == 8_000_000
    assert result["estimated_cost_per_attendee"] == 400_000
    assert result["margin_amount_per_attendee"] == 100_000
    assert result["pool_fee"] == 5000


def test_cost_plus_percentage_margin_uses_basis_points_in_storage():
    result = normalize_pricing_payload(
        _base_payload(margin_type="percentage", margin_value=25)
    )

    assert result["margin_value"] == 2500
    assert result["margin_amount_per_attendee"] == 100_000
    assert result["pool_fee"] == 5000


def test_manual_mode_keeps_existing_booking_price():
    result = normalize_pricing_payload(
        _base_payload(pricing_mode="manual", pool_fee=6250)
    )

    assert "pool_fee" not in result
    assert result["estimated_cost_per_attendee"] == 400_000
