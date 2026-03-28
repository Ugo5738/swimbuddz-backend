"""Status classification and confidence bands.

Confidence bands are wider when we have less data, reflecting honest
uncertainty.  As months of real data accumulate, bands narrow.

    uncertainty = 0.35 * e^(-0.08 * months) + 0.10

    0 months  → ±45%
    6 months  → ±32%
    12 months → ±24%
    24 months → ±15%
"""

from __future__ import annotations

import math


def uncertainty_factor(months_of_data: int) -> float:
    """Return the half-width of the confidence band as a fraction.

    E.g. 0.30 means the band is ±30% of the expected value.
    """
    return 0.35 * math.exp(-0.08 * months_of_data) + 0.10


def confidence_band(expected: float, months_of_data: int) -> tuple[float, float]:
    """Compute lower and upper confidence bounds.

    Returns (lower, upper) as absolute values (not percentages).
    """
    u = uncertainty_factor(months_of_data)
    lower = expected * (1 - u)
    upper = expected * (1 + u)
    return (round(max(lower, 0), 1), round(upper, 1))


def demand_level(seasonal_index: float) -> str:
    """Categorise a seasonal index into a named demand level.

    Returns one of: "low", "moderate", "high", "peak".
    """
    if seasonal_index < 0.80:
        return "low"
    elif seasonal_index < 0.95:
        return "moderate"
    elif seasonal_index < 1.10:
        return "high"
    else:
        return "peak"


def classify_status(
    actual: float | None,
    expected: float,
    lower: float,
    upper: float,
    seasonal_index: float,
) -> str:
    """Classify a month's performance status.

    For future months (actual=None), classifies based on expected level.
    For past months, compares actual to the confidence band.

    Returns one of:
        "expected_seasonal_dip" — future month with below-average expected demand
        "on_track"             — actual within confidence band (or future average+ month)
        "outperforming"        — actual above upper bound
        "underperforming"      — actual below lower bound
    """
    if actual is None:
        # Future month — classify the expectation itself
        if seasonal_index < 0.90:
            return "expected_seasonal_dip"
        return "on_track"

    if actual > upper:
        return "outperforming"
    elif actual < lower:
        return "underperforming"
    else:
        return "on_track"
