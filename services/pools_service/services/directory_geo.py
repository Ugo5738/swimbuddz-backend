"""Geographic distance and identity matching for public pool discovery.

Match candidates only within the same locality. A similar name alone must
never collapse different branches in different towns.
"""
import re
from difflib import SequenceMatcher
from math import radians

from sqlalchemy import func

GENERIC_WORDS = {"pool", "swimming", "swim", "centre", "center", "facility"}


def normalize_pool_name(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.casefold().replace("&", " and "))
    significant = [word for word in words if word not in GENERIC_WORDS]
    return " ".join(significant or words)


def normalize_location(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def likely_same_pool(
    name: str,
    area: str | None,
    other_name: str,
    other_area: str | None,
    address: str | None = None,
    other_address: str | None = None,
) -> bool:
    first_area, second_area = normalize_location(area), normalize_location(other_area)
    if not first_area or not second_area or first_area != second_area:
        return False
    first, second = normalize_pool_name(name), normalize_pool_name(other_name)
    if first == second:
        return True
    similarity = SequenceMatcher(None, first, second).ratio()
    if similarity >= 0.91:
        return True
    if similarity >= 0.82 and address and other_address:
        return SequenceMatcher(
            None, normalize_location(address), normalize_location(other_address)
        ).ratio() >= 0.85
    return False


def distance_km_sql(pool, latitude: float, longitude: float):
    """Great-circle distance on a sphere; PostgreSQL expression in kilometres.

    Clamp cosine to [-1, 1] to prevent floating-point acos domain errors.
    Coordinates are never interpreted as driving-time estimates.
    """
    latitude_radians = radians(latitude)
    longitude_radians = radians(longitude)
    cosine = (
        func.sin(latitude_radians) * func.sin(func.radians(pool.latitude))
        + func.cos(latitude_radians)
        * func.cos(func.radians(pool.latitude))
        * func.cos(func.radians(pool.longitude) - longitude_radians)
    )
    return 6371.0088 * func.acos(func.least(1.0, func.greatest(-1.0, cosine)))
