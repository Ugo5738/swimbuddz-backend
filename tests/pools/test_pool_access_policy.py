"""Pool Access cost and signed admission invariants."""

import uuid
import pytest
from fastapi import HTTPException
from services.pools_service.services.access_policy import (
    quoted_amount,
    reconciled_cost,
    ticket_for,
    admission_id_from_ticket,
)
from services.pools_service.schemas.access import OfferOut


def test_per_person_settlement_counts_only_verified_entries():
    assert reconciled_cost(500000, "per_person", 2) == 1000000
    assert reconciled_cost(500000, "per_person", 0) == 0


def test_per_group_settlement_charges_once_after_first_entry():
    assert reconciled_cost(500000, "per_group", 5) == 500000
    assert reconciled_cost(500000, "per_group", 0) == 0


def test_price_validation():
    assert quoted_amount(700000, 2) == 1400000
    for qty in (0, 26):
        with pytest.raises(ValueError):
            quoted_amount(700000, qty)


def test_tickets_are_tamper_evident(monkeypatch):
    monkeypatch.setenv("POOL_ACCESS_QR_SECRET", "0123456789abcdef0123456789abcdef")
    admission = uuid.uuid4()
    ticket = ticket_for(admission)
    assert admission_id_from_ticket(ticket) == admission
    with pytest.raises(HTTPException):
        admission_id_from_ticket(ticket[:-1] + ("0" if ticket[-1] != "0" else "1"))


def test_qr_fails_closed_without_secret(monkeypatch):
    monkeypatch.delenv("POOL_ACCESS_QR_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc:
        ticket_for(uuid.uuid4())
    assert exc.value.status_code == 503


def test_public_offer_cannot_leak_partner_cost():
    assert "negotiated_cost_kobo" not in OfferOut.model_fields
    assert "cost_basis" not in OfferOut.model_fields
