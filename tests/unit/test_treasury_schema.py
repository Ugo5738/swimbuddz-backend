# fmt: off
"""Treasury schemas reject unsafe transfer shapes and malformed inputs."""
from datetime import date
from uuid import uuid4
import pytest
from pydantic import ValidationError
from services.ledger_service.schemas.treasury import BankCreate, BudgetCreate, RequestCreate, StatementImport, ForecastCreate, SnapshotCreate, PostConfirmedRequest

def _request(**overrides):
    data = {"kind": "funding", "amount_minor": 100_000, "purpose": "Pool allocations",
            "category": "pool_fees", "source_bank_id": uuid4(), "destination_bank_id": uuid4()}
    data.update(overrides)
    return data

def test_no_self_bank_transfer():
    bank_id = uuid4()
    with pytest.raises(ValidationError):
        RequestCreate(**_request(source_bank_id=bank_id, destination_bank_id=bank_id))

def test_funding_needs_destination():
    with pytest.raises(ValidationError):
        RequestCreate(**_request(destination_bank_id=None))

def test_expense_must_not_have_destination():
    with pytest.raises(ValidationError):
        RequestCreate(**_request(kind="expense"))

def test_budget_must_have_valid_dates_and_amount():
    with pytest.raises(ValidationError):
        BudgetCreate(name="Pool fees", category="pool", amount_minor=0,
                     starts_on=date(2026, 12, 1), ends_on=date(2026, 11, 1))

@pytest.mark.parametrize("amount", [0, -100, 100])
def test_statement_signed_amount(amount):
    payload = {"statement_id": "bank-2026-11", "rows": [
        {"row_number": 1, "occurred_on": "2026-11-17", "amount_minor": amount,
         "description": "Approved receipt"}]}
    if amount == 0:
        with pytest.raises(ValidationError):
            StatementImport(**payload)
    else:
        assert StatementImport(**payload).rows[0].amount_minor == amount

def test_known_bank_codes_only():
    with pytest.raises(ValidationError):
        BankCreate(code="personal")


def test_expense_documents_and_programme_are_structured():
    req = RequestCreate(
        kind="expense", amount_minor=150000, purpose="Pool hire for Academy",
        category="pool_fees", source_bank_id=uuid4(),
        vendor_name="Pool operator", invoice_reference="POOL-17",
        programme="academy", cost_center_code="YABA",
    )
    assert req.vendor_name == "Pool operator"
    assert req.programme == "academy"
    assert req.destination_bank_id is None

def test_forecast_is_not_negative_and_requires_valid_scenario():
    with pytest.raises(ValidationError):
        ForecastCreate(direction="outflow", scenario="base", effective_on="2026-12-01",
                       amount_minor=-100, purpose="Coaching fee")
    with pytest.raises(ValidationError):
        ForecastCreate(direction="outflow", scenario="certain", effective_on="2026-12-01",
                       amount_minor=100, purpose="Coaching fee")

def test_snapshot_accepts_signed_balance_with_original_evidence_id():
    item = SnapshotCreate(as_of=date(2026, 11, 17), balance_minor=-1500,
                          evidence_reference="stanbic-statement-nov-17")
    assert item.balance_minor == -1500

def test_ledger_posting_requires_explicit_owner_acknowledgement():
    assert PostConfirmedRequest().acknowledgement is False
