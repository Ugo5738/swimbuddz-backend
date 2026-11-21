"""Bank registration contracts for the reusable Treasury account registry."""

import pytest
from pydantic import ValidationError

from services.ledger_service.schemas.treasury import BankCreate, BankUpdate


def test_existing_stanbic_and_moniepoint_requests_are_backward_compatible():
    assert BankCreate(code="stanbic").code == "stanbic"
    assert BankCreate(code="moniepoint", last_four="1234").last_four == "1234"


def test_register_new_bank_with_explicit_purpose_and_masked_account_number():
    item = BankCreate(
        bank_name="  Example Commercial Bank  ", purpose="operations", last_four="9876"
    )
    assert item.code is None
    assert item.bank_name == "Example Commercial Bank"
    assert item.purpose == "operations"
    assert item.last_four == "9876"


@pytest.mark.parametrize(
    "payload",
    [
        {"bank_name": "Example Bank"},
        {"purpose": "treasury"},
        {"bank_name": " " * 4, "purpose": "operations"},
        {"bank_name": "Example Bank", "purpose": "other"},
        {
            "bank_name": "Example Bank",
            "purpose": "operations",
            "last_four": "1234567890",
        },
        {"code": "stanbic", "bank_name": "Another Bank"},
        {"code": "some-bank"},
    ],
)
def test_reject_missing_invalid_or_spoofed_registration_fields(payload):
    with pytest.raises(ValidationError):
        BankCreate(**payload)


def test_bank_details_update_stores_only_masked_account_digits():
    assert BankUpdate(last_four="4512").last_four == "4512"
    with pytest.raises(ValidationError):
        BankUpdate(last_four="1234567890")
