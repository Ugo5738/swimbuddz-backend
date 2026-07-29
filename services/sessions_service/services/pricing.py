"""Normalize editable session cost lines into an auditable price snapshot."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

PRICING_KEYS = {
    "pricing_mode",
    "pricing_expected_attendees",
    "cost_lines",
    "margin_type",
    "margin_value",
}


def _kobo(value: Any) -> int:
    return int(
        (Decimal(str(value or 0)) * Decimal(100)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _naira(value: int) -> float:
    return value / 100


def normalize_pricing_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return DB-ready pricing fields and a Naira booking-price override."""
    expected = int(
        payload.get("pricing_expected_attendees") or payload.get("capacity") or 1
    )
    expected = max(expected, 1)
    normalized_lines: list[dict[str, Any]] = []
    total_cost_kobo = 0

    for raw in payload.get("cost_lines") or []:
        unit_cost_kobo = _kobo(raw.get("unit_cost_naira"))
        quantity = Decimal(str(raw.get("quantity") or 0))
        total_kobo = int(
            (Decimal(unit_cost_kobo) * quantity).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
        total_cost_kobo += total_kobo
        normalized_lines.append(
            {
                "category": str(raw.get("category") or "other"),
                "description": str(raw.get("description") or "Other cost"),
                "charge_basis": str(raw.get("charge_basis") or "flat_session"),
                "unit_cost_kobo": unit_cost_kobo,
                "quantity": float(quantity),
                "total_cost_kobo": total_kobo,
                "source_rate_type": raw.get("source_rate_type"),
                "source_rate_id": (
                    str(raw["source_rate_id"]) if raw.get("source_rate_id") else None
                ),
            }
        )

    cost_per_attendee = int(
        (Decimal(total_cost_kobo) / Decimal(expected)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )
    margin_type = str(payload.get("margin_type") or "fixed_per_attendee")
    margin_input = Decimal(str(payload.get("margin_value") or 0))
    if margin_type == "percentage":
        margin_value = int(
            (margin_input * Decimal(100)).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
        margin_amount = int(
            (
                Decimal(cost_per_attendee) * Decimal(margin_value) / Decimal(10_000)
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
    else:
        margin_type = "fixed_per_attendee"
        margin_value = _kobo(margin_input)
        margin_amount = margin_value

    result = {
        "pricing_mode": str(payload.get("pricing_mode") or "manual"),
        "pricing_expected_attendees": expected,
        "cost_lines": normalized_lines,
        "estimated_total_cost": total_cost_kobo,
        "estimated_cost_per_attendee": cost_per_attendee,
        "margin_type": margin_type,
        "margin_value": margin_value,
        "margin_amount_per_attendee": margin_amount,
    }
    if result["pricing_mode"] == "cost_plus":
        result["pool_fee"] = _naira(cost_per_attendee + margin_amount)
    return result


def pricing_payload_from_session(session) -> dict[str, Any]:
    """Convert a stored snapshot back to the API input representation."""
    lines = []
    for raw in session.cost_lines or []:
        lines.append(
            {
                "category": raw.get("category"),
                "description": raw.get("description"),
                "charge_basis": raw.get("charge_basis"),
                "unit_cost_naira": _naira(raw.get("unit_cost_kobo") or 0),
                "quantity": raw.get("quantity") or 0,
                "source_rate_type": raw.get("source_rate_type"),
                "source_rate_id": raw.get("source_rate_id"),
            }
        )
    margin_value = (
        (session.margin_value or 0) / 100
        if session.margin_type == "percentage"
        else _naira(session.margin_value or 0)
    )
    return {
        "pricing_mode": session.pricing_mode,
        "pricing_expected_attendees": session.pricing_expected_attendees,
        "cost_lines": lines,
        "margin_type": session.margin_type,
        "margin_value": margin_value,
        "capacity": session.capacity,
    }


def pricing_response_fields(session) -> dict[str, Any]:
    """Convert stored kobo snapshot fields to API-facing Naira values."""
    state = pricing_payload_from_session(session)
    return {
        **state,
        "estimated_total_cost": _naira(session.estimated_total_cost or 0),
        "estimated_cost_per_attendee": _naira(session.estimated_cost_per_attendee or 0),
        "margin_amount_per_attendee": _naira(session.margin_amount_per_attendee or 0),
    }
