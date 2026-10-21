"""Public directory contracts must not expose operational pool records."""

import uuid

from services.pools_service.schemas.directory import (
    DirectoryPool,
    DirectoryReviewCreate,
    DirectorySuggestion,
)


def test_public_contract_excludes_internal_terms():
    fields = set(DirectoryPool.model_fields)
    for internal in (
        "contact_person",
        "contact_phone",
        "contact_email",
        "price_per_swimmer_ngn",
        "flat_session_fee_ngn",
        "computed_score",
        "overall_score",
        "management_cooperation",
        "notes",
        "negotiated_cost_kobo",
        "partnership_potential",
    ):
        assert internal not in fields
    item = DirectoryPool(
        id=uuid.uuid4(),
        name="Test Pool",
        slug="test-pool",
        is_partner=False,
        bookable=False,
    )
    assert item.bookable is False
    assert "price_per_swimmer_ngn" not in item.model_dump()


def test_review_validation_rejects_short_and_invalid_scores():
    from pydantic import ValidationError

    for payload in (
        {"rating": 0, "review": "A detailed and genuinely useful review."},
        {"rating": 6, "review": "A detailed and genuinely useful review."},
        {"rating": 4, "review": "Too short"},
    ):
        try:
            DirectoryReviewCreate(**payload)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"Should reject: {payload}")


def test_suggestion_requires_pool_and_neighborhood():
    from pydantic import ValidationError

    try:
        DirectorySuggestion(pool_name="One", location_area="")
    except ValidationError:
        pass
    else:
        raise AssertionError("Location must be required for duplicate detection")
