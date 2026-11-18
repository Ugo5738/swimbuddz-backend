"""Moving-average cost must preserve currency precision and remaining value."""

import pytest

from services.store_service.services.inventory_costing import consumption_cost_minor


def test_moving_average_cost_with_nondivisible_kobo():
    assert consumption_cost_minor(10001, 3, 1) == 3333
    assert consumption_cost_minor(6668, 2, 2) == 6668
    assert consumption_cost_minor(10500, 5, 2) == 4200


@pytest.mark.parametrize(
    "total,stock,sold", [(-1, 2, 1), (100, 0, 1), (100, 1, 2), (100, 2, 0)]
)
def test_invalid_stock_or_sale_is_rejected(total, stock, sold):
    with pytest.raises(ValueError):
        consumption_cost_minor(total, stock, sold)
