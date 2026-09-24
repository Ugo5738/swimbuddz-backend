"""Payment snapshots must retain totals and exact allocation across retries."""

import pytest

from services.sessions_service.services.booking_confirmation import allocate_total


@pytest.mark.parametrize(
    "total,weights,expected",
    [
        (1, [1, 1, 1], [1, 0, 0]),
        (2, [1, 1, 1, 1], [1, 1, 0, 0]),
        (350001, [2, 1], [233334, 116667]),
        (7, [0, 0, 0], [3, 2, 2]),
        (0, [2, 0, 1], [0, 0, 0]),
    ],
)
def test_bundle_allocations_preserve_every_unit(total, weights, expected):
    assert allocate_total(total, weights) == expected
    assert sum(expected) == total
    assert all(value >= 0 for value in expected)
