"""Pool scoring helpers.

Computes a weighted composite score from the 6 component rating fields on a pool.
Weights vary by `pool_type` so that the factors that matter most for each pool's
primary audience get more pull. The resulting score is stored in `Pool.computed_score`
for reporting and data-quality comparison against `overall_score` (admin judgment).
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from services.pools_service.models.enums import PoolType

# Component field names, in a stable order.
SCORE_COMPONENTS: tuple[str, ...] = (
    "water_quality",
    "good_for_beginners",
    "good_for_training",
    "ease_of_access",
    "management_cooperation",
    "partnership_potential",
)

# Default weight is 1.0 for every component. Per pool_type we raise a few to 1.5
# to reflect what matters most for that audience.
#   Academy: beginners + training are paramount (swim school context)
#   Club: training + partnership potential (ongoing sessions matter most)
#   Community: access + water quality (public-facing, drop-in use)
#   Hotel: water quality (guest-safety / image)
#   Public: access (geographic reach matters)
#   Private: use defaults (small / niche)
_WEIGHT_OVERRIDES: dict[str, dict[str, float]] = {
    PoolType.ACADEMY.value: {"good_for_beginners": 1.5, "good_for_training": 1.5},
    PoolType.CLUB.value: {"good_for_training": 1.5, "partnership_potential": 1.5},
    PoolType.COMMUNITY.value: {"ease_of_access": 1.5, "water_quality": 1.5},
    PoolType.HOTEL.value: {"water_quality": 1.5},
    PoolType.PUBLIC.value: {"ease_of_access": 1.5},
    PoolType.PRIVATE.value: {},
}


def _weights_for(pool_type: Optional[str]) -> dict[str, float]:
    """Return the weight map for a given pool_type (str or enum value)."""
    defaults = {c: 1.0 for c in SCORE_COMPONENTS}
    if not pool_type:
        return defaults
    key = pool_type.value if hasattr(pool_type, "value") else str(pool_type)
    overrides = _WEIGHT_OVERRIDES.get(key, {})
    return {**defaults, **overrides}


def compute_pool_score(
    *,
    water_quality: Optional[int] = None,
    good_for_beginners: Optional[int] = None,
    good_for_training: Optional[int] = None,
    ease_of_access: Optional[int] = None,
    management_cooperation: Optional[int] = None,
    partnership_potential: Optional[int] = None,
    pool_type: Optional[object] = None,
) -> Optional[Decimal]:
    """Compute the weighted composite score.

    - Only present (non-None) components contribute. Missing components don't
      drag the score toward zero — we normalise by the sum of present weights.
    - Weights vary by pool_type (see _WEIGHT_OVERRIDES).
    - Result is a Decimal rounded to 2 dp in the range [1.00, 5.00], or None
      if no component scores are set.
    """
    scores = {
        "water_quality": water_quality,
        "good_for_beginners": good_for_beginners,
        "good_for_training": good_for_training,
        "ease_of_access": ease_of_access,
        "management_cooperation": management_cooperation,
        "partnership_potential": partnership_potential,
    }
    present = {k: v for k, v in scores.items() if v is not None}
    if not present:
        return None

    weights = _weights_for(pool_type)
    weighted_sum = sum(v * weights[k] for k, v in present.items())
    total_weight = sum(weights[k] for k in present)
    if total_weight == 0:
        return None

    raw = weighted_sum / total_weight
    # Round half-up to 2dp, which is more intuitive than banker's rounding.
    return Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def recompute_pool_score(pool) -> Optional[Decimal]:
    """Convenience wrapper that takes a Pool ORM instance.

    Mutates nothing — returns the computed Decimal (or None). Caller is
    responsible for assigning to `pool.computed_score`.
    """
    return compute_pool_score(
        water_quality=pool.water_quality,
        good_for_beginners=pool.good_for_beginners,
        good_for_training=pool.good_for_training,
        ease_of_access=pool.ease_of_access,
        management_cooperation=pool.management_cooperation,
        partnership_potential=pool.partnership_potential,
        pool_type=pool.pool_type,
    )
