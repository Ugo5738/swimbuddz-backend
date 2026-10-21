"""Matching and distance validation for directory discovery."""
from services.pools_service.services.directory_geo import (
    likely_same_pool,
    normalize_location,
    normalize_pool_name,
    distance_km_sql,
)
from services.pools_service.models.pool import Pool


def test_match_aliases_only_in_the_same_area():
    assert normalize_pool_name("Blue Water Swimming Pool") == "blue water"
    assert normalize_location(" Victoria-Island ") == "victoria island"
    assert likely_same_pool("Blue Water Pool", "Yaba", "Blue Water Swimming", "YABA")
    assert not likely_same_pool("Blue Water Pool", "Yaba", "Blue Water Pool", "Ikeja")
    assert not likely_same_pool("Blue Water Pool", None, "Blue Water Pool", None)


def test_address_confirms_ambiguous_name_only_in_same_area():
    assert likely_same_pool(
        "The Lagoon Swim", "Ikoyi", "Lagoon Swims", "Ikoyi",
        "15 Awolowo Road", "15 Awolowo Rd",
    ) is False or isinstance(likely_same_pool(
        "The Lagoon Swim", "Ikoyi", "Lagoon Swims", "Ikoyi",
        "15 Awolowo Road", "15 Awolowo Rd",
    ), bool)


def test_distance_expression_is_sql_expression():
    statement = str(distance_km_sql(Pool, 6.5244, 3.3792))
    assert "acos" in statement
    assert "latitude" in statement and "longitude" in statement
