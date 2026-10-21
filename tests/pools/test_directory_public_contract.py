"""Prevent confidential pool data from leaking through old and new public APIs."""
from services.pools_service.schemas.public_partner import PublicPartnerPool
from services.pools_service.schemas.directory import DirectoryPool


def test_public_models_are_explicit_allowlists():
    forbidden = {
        "price_per_swimmer_ngn",
        "flat_session_fee_ngn",
        "notes",
        "contact_person",
        "contact_phone",
        "contact_email",
        "water_quality",
        "management_cooperation",
        "partnership_potential",
        "computed_score",
        "overall_score",
        "available_days_times",
    }
    assert not forbidden.intersection(PublicPartnerPool.model_fields)
    assert not forbidden.intersection(DirectoryPool.model_fields)
