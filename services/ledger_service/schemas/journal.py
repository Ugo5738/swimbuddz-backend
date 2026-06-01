"""Pydantic schemas for journal-entry posting (the internal route contract).

Mirrors the payload built by libs/common/ledger_client.post_journal_entry and
design doc §6. Validation here is the first gate; the posting service re-checks
against the DB (accounts exist, period open, etc.).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class JournalLineInput(BaseModel):
    """One debit or credit line. Exactly one of debit/credit must be > 0."""

    account_ref: str = Field(
        ..., description="Stable CoA maps_to ref, e.g. 'paystack_clearing'"
    )
    debit: int = Field(0, ge=0, description="Minor units (kobo); 0 for a credit line")
    credit: int = Field(0, ge=0, description="Minor units (kobo); 0 for a debit line")
    currency: Optional[str] = Field(
        None, min_length=3, max_length=3, description="ISO 4217; defaults to org base"
    )
    cost_center: Optional[str] = None
    dimension_1: Optional[str] = None
    dimension_2: Optional[str] = None
    member_ref: Optional[str] = None
    external_ref: Optional[str] = None
    tax_code_ref: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one_side(self) -> "JournalLineInput":
        if (self.debit > 0) == (self.credit > 0):
            raise ValueError("each line must have exactly one of debit/credit > 0")
        return self


class JournalEntryCreate(BaseModel):
    """A balanced journal entry to post. sum(debits) must equal sum(credits)."""

    idempotency_key: str = Field(..., min_length=1)
    entry_date: date
    description: str = Field(..., min_length=1)
    source_service: str = Field(..., min_length=1)
    source_type: str = Field(..., min_length=1)
    source_id: Optional[str] = None
    org_id: Optional[uuid.UUID] = Field(
        None, description="Phase 1: ignored; server uses LEDGER_DEFAULT_ORG_ID"
    )
    metadata: Optional[dict] = None
    lines: list[JournalLineInput] = Field(..., min_length=2)

    @model_validator(mode="after")
    def _balanced(self) -> "JournalEntryCreate":
        total_debit = sum(line.debit for line in self.lines)
        total_credit = sum(line.credit for line in self.lines)
        if total_debit != total_credit:
            raise ValueError(
                f"entry not balanced: debits {total_debit} != credits {total_credit}"
            )
        return self


class JournalEntryResult(BaseModel):
    """Returned on successful post (or idempotent replay)."""

    entry_id: uuid.UUID
    status: str
    period_id: uuid.UUID
    idempotent_replay: bool = False
