"""Deterministic moving weighted-average cost in integer kobo.

Remainder stays in stock until the last unit sells, so cost is conserved.
"""


def consumption_cost_minor(
    total_cost_minor: int, stock_quantity: int, sold_quantity: int
) -> int:
    if total_cost_minor < 0 or stock_quantity <= 0:
        raise ValueError("Valid nonnegative stock value and positive quantity required")
    if sold_quantity <= 0 or sold_quantity > stock_quantity:
        raise ValueError("Sales quantity must be within available stock")
    if sold_quantity == stock_quantity:
        return total_cost_minor
    return total_cost_minor * sold_quantity // stock_quantity
