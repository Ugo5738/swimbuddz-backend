"""Normalize editable event costs and margins into an auditable snapshot."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

PRICING_KEYS = {
    "cost_naira",
    "pricing_mode",
    "pricing_expected_attendees",
    "cost_lines",
    "margin_type",
    "margin_value",
}


def _kobo(value: Any) -> int:
    return int(
        (Decimal(str(value or 0)) * Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _naira(value: int) -> float:
    return value / 100


def normalize_event_pricing(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("pricing_mode") or "fixed")
    expected = int(
        payload.get("pricing_expected_attendees") or payload.get("max_capacity") or 1
    )
    expected = max(expected, 1)
    normalized_lines: list[dict[str, Any]] = []
    total_cost_kobo = 0

    for raw in payload.get("cost_lines") or []:
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump(mode="json")
        unit_cost_kobo = _kobo(raw.get("unit_cost_naira"))
        quantity = Decimal(str(raw.get("quantity") or 0))
        total_kobo = int(
            (Decimal(unit_cost_kobo) * quantity).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
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
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    margin_type = str(payload.get("margin_type") or "fixed_per_attendee")
    margin_input = Decimal(str(payload.get("margin_value") or 0))
    if margin_type == "percentage":
        margin_value = int(
            (margin_input * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
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

    cost_kobo = None
    if mode == "fixed" and payload.get("cost_naira") is not None:
        cost_kobo = _kobo(payload["cost_naira"])
    elif mode == "cost_plus":
        cost_kobo = cost_per_attendee + margin_amount
    elif mode not in {"free", "included"}:
        mode = "fixed"

    return {
        "pricing_mode": mode,
        "pricing_expected_attendees": expected,
        "cost_lines": normalized_lines,
        "estimated_total_cost": total_cost_kobo,
        "estimated_cost_per_attendee": cost_per_attendee,
        "margin_type": margin_type,
        "margin_value": margin_value,
        "margin_amount_per_attendee": margin_amount,
        "cost_kobo": cost_kobo,
    }


def event_pricing_payload(event) -> dict[str, Any]:
    lines = []
    for raw in event.cost_lines or []:
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
        (event.margin_value or 0) / 100
        if event.margin_type == "percentage"
        else _naira(event.margin_value or 0)
    )
    return {
        "cost_naira": _naira(event.cost_kobo) if event.cost_kobo is not None else None,
        "pricing_mode": event.pricing_mode,
        "pricing_expected_attendees": event.pricing_expected_attendees,
        "cost_lines": lines,
        "margin_type": event.margin_type,
        "margin_value": margin_value,
        "max_capacity": event.max_capacity,
    }


def event_pricing_response(event) -> dict[str, Any]:
    state = event_pricing_payload(event)
    state.pop("max_capacity", None)
    return {
        **state,
        "estimated_total_cost_naira": _naira(event.estimated_total_cost or 0),
        "estimated_cost_per_attendee_naira": _naira(
            event.estimated_cost_per_attendee or 0
        ),
        "margin_amount_per_attendee_naira": _naira(
            event.margin_amount_per_attendee or 0
        ),
    }
