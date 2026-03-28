"""Prior ↔ data blending for cold-start calibration.

With <12 months of data, the model leans on Lagos domain priors.
As real observations accumulate, the model gradually shifts weight
to the data.  The blending follows a Bayesian-inspired schedule:

    prior_weight = max(0.05, 1 / (1 + months_of_data / 4))

    0 months  → 100% prior
    4 months  → 50% prior
    12 months → ~25% prior
    24 months → ~14% prior
"""

from __future__ import annotations

from services.reporting_service.services.seasonality.priors import (
    DEFAULT_BASELINE_ATTENDANCE,
    LAGOS_SEASONAL_INDICES,
)


def prior_weight(months_of_data: int) -> float:
    """Compute how much to trust the prior vs observed data.

    Returns a value in [0.05, 1.0].
    """
    return max(0.05, 1.0 / (1.0 + months_of_data / 4.0))


def blend_prior_and_data(
    prior_value: float,
    observed_value: float | None,
    months_of_data: int,
) -> float:
    """Blend a prior estimate with an observed value.

    If observed_value is None (no data yet), returns the prior unchanged.
    """
    if observed_value is None:
        return prior_value
    w = prior_weight(months_of_data)
    return w * prior_value + (1 - w) * observed_value


def estimate_baseline(
    actuals: list[dict] | None = None,
    seasonal_indices: dict[int, float] | None = None,
) -> float:
    """Estimate the de-seasonalised baseline from actuals.

    Each actual dict must have keys: month (int), total_attendance (int).
    We de-seasonalise each observation by dividing by its seasonal index,
    then take the mean.

    If no actuals, returns the domain default.
    """
    if not actuals:
        return float(DEFAULT_BASELINE_ATTENDANCE)

    if seasonal_indices is None:
        seasonal_indices = dict(LAGOS_SEASONAL_INDICES)

    deseasonalised = []
    for a in actuals:
        m = a["month"]
        s = seasonal_indices.get(m, 1.0)
        if s > 0:
            deseasonalised.append(a["total_attendance"] / s)

    if not deseasonalised:
        return float(DEFAULT_BASELINE_ATTENDANCE)

    return sum(deseasonalised) / len(deseasonalised)


def calibrate_seasonal_indices(
    actuals: list[dict] | None = None,
    baseline: float | None = None,
) -> dict[int, float]:
    """Produce calibrated seasonal indices by blending priors with data.

    Each actual dict must have keys: month (int), total_attendance (int).
    Observed index for a month = actual_attendance / baseline.
    We then blend this with the prior index.

    Returns 12 indices keyed by month (1–12).
    """
    prior = dict(LAGOS_SEASONAL_INDICES)

    if not actuals:
        return prior

    if baseline is None:
        baseline = estimate_baseline(actuals)

    # Group actuals by month
    by_month: dict[int, list[float]] = {}
    for a in actuals:
        m = a["month"]
        by_month.setdefault(m, []).append(a["total_attendance"])

    months_of_data = len(actuals)
    result = {}
    for m in range(1, 13):
        observed_index = None
        if m in by_month and baseline > 0:
            avg_actual = sum(by_month[m]) / len(by_month[m])
            observed_index = avg_actual / baseline

        result[m] = round(
            blend_prior_and_data(prior[m], observed_index, months_of_data),
            3,
        )

    # Normalise so mean ≈ 1.0
    mean_idx = sum(result.values()) / 12
    if mean_idx > 0:
        result = {m: round(v / mean_idx, 3) for m, v in result.items()}

    return result
