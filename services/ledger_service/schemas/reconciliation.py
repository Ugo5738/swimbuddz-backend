"""Schemas for settlement reconciliation (R3-PR2, design §11.2).

``ExternalTransactionBatch`` is the intake payload other services push to
``POST /internal/ledger/external-transactions``. The admin read returns open
breaks plus a small summary.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ExternalTransactionIn(BaseModel):
    """One PSP settlement transaction pushed for reconciliation."""

    psp: str = Field(..., min_length=1)
    external_txn_id: str = Field(..., min_length=1)
    external_ref: Optional[str] = None
    settlement_ref: Optional[str] = None
    amount_minor: int = 0
    fee_minor: int = 0
    currency: str = "NGN"
    status: Optional[str] = None
    occurred_at: Optional[datetime] = None
    raw_payload: Optional[dict] = None


class ExternalTransactionBatch(BaseModel):
    """Bulk intake of PSP transactions. Idempotent per (org, psp, txn id)."""

    org_id: Optional[uuid.UUID] = Field(
        None, description="Phase 1: ignored; server uses LEDGER_DEFAULT_ORG_ID"
    )
    transactions: list[ExternalTransactionIn] = Field(default_factory=list)


class ReconciliationIntakeResult(BaseModel):
    received: int
    inserted: int
    matched: int
    breaks_opened: int


class ReconciliationBreakOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    break_type: str
    psp: Optional[str] = None
    external_ref: Optional[str] = None
    external_txn_id: Optional[str] = None
    settlement_ref: Optional[str] = None
    expected_minor: Optional[int] = None
    actual_minor: Optional[int] = None
    currency: str
    status: str
    detail: Optional[str] = None
    created_at: datetime


class ReconciliationReport(BaseModel):
    open_breaks: int
    open_break_amount_minor: int
    matched_count: int
    unmatched_count: int
    breaks: list[ReconciliationBreakOut]
