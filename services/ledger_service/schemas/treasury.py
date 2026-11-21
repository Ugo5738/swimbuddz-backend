# fmt: off
"""Validated schemas for internal treasury operations. Amounts are integer kobo."""
from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BankCreate(BaseModel):
    """Register a bank account, not a live bank-feed connection.

    Old clients can still submit the two legacy codes. New accounts receive
    their own stable code, so one company may hold multiple accounts at a bank.
    """

    code: Optional[Literal["stanbic", "moniepoint"]] = None
    bank_name: Optional[str] = Field(None, min_length=2, max_length=120)
    purpose: Optional[Literal["treasury", "operations"]] = None
    last_four: Optional[str] = Field(None, pattern=r"^[0-9]{4}$")

    @model_validator(mode="after")
    def validate_registration(self):
        if self.code is None:
            if not self.bank_name or not self.bank_name.strip() or self.purpose is None:
                raise ValueError("New bank accounts require a bank name and purpose")
            self.bank_name = self.bank_name.strip()
        elif self.bank_name is not None or self.purpose is not None:
            raise ValueError("Legacy bank registration cannot override institution or purpose")
        return self


class BankUpdate(BaseModel):
    """Owner-only update of non-financial account metadata."""

    last_four: Optional[str] = Field(None, pattern=r"^[0-9]{4}$")


class BankOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    bank_name: str
    purpose: str
    last_four: Optional[str] = None
    ledger_account_id: Optional[UUID] = None
    statement_balance_minor: Optional[int] = None
    statement_as_of: Optional[date] = None
    statement_verified_at: Optional[datetime] = None


class StatementItem(BaseModel):
    row_number: int = Field(ge=1)
    occurred_on: date
    amount_minor: int
    description: str = Field(min_length=1, max_length=600)
    reference: Optional[str] = Field(None, max_length=180)

    @model_validator(mode="after")
    def nonzero(self):
        if self.amount_minor == 0:
            raise ValueError("Statement rows must have a nonzero signed amount")
        return self


class StatementImport(BaseModel):
    statement_id: str = Field(min_length=3, max_length=160)
    rows: list[StatementItem] = Field(min_length=1, max_length=250)


class BudgetCreate(BaseModel):
    kind: Literal["expense", "funding"] = "expense"
    name: str = Field(min_length=3, max_length=160)
    category: str = Field(min_length=2, max_length=80)
    amount_minor: int = Field(gt=0)
    starts_on: date
    ends_on: date

    @model_validator(mode="after")
    def dates_valid(self):
        if self.ends_on < self.starts_on:
            raise ValueError("End date cannot precede start date")
        return self


class RequestCreate(BaseModel):
    vendor_name: Optional[str] = Field(None, max_length=180)
    invoice_reference: Optional[str] = Field(None, max_length=120)
    evidence_reference: Optional[str] = Field(None, max_length=250)
    programme: Optional[str] = Field(None, max_length=80)
    cost_center_code: Optional[str] = Field(None, max_length=80)
    service_on: Optional[date] = None
    kind: Literal["expense", "funding"]
    amount_minor: int = Field(gt=0)
    purpose: str = Field(min_length=5, max_length=1000)
    category: str = Field(min_length=2, max_length=80)
    source_bank_id: UUID
    destination_bank_id: Optional[UUID] = None
    budget_id: Optional[UUID] = None

    @model_validator(mode="after")
    def transfer_destination(self):
        if self.kind == "funding" and not self.destination_bank_id:
            raise ValueError("Funding transfers require a destination bank account")
        if self.kind == "expense" and self.destination_bank_id:
            raise ValueError("Expenses cannot have a bank destination")
        if self.source_bank_id == self.destination_bank_id:
            raise ValueError("A transfer cannot have the same source and destination")
        return self


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    status: str
    amount_minor: int
    purpose: str
    category: str
    source_bank_id: UUID
    destination_bank_id: Optional[UUID]
    budget_id: Optional[UUID]
    requested_by: UUID
    approved_by: Optional[UUID]
    approved_at: Optional[datetime]
    rejected_reason: Optional[str]
    approval_note: Optional[str] = None
    bank_line_id: Optional[UUID]
    vendor_name: Optional[str] = None
    invoice_reference: Optional[str] = None
    evidence_reference: Optional[str] = None
    programme: Optional[str] = None
    cost_center_code: Optional[str] = None
    service_on: Optional[date] = None
    journal_entry_id: Optional[UUID] = None
    ledger_posted_at: Optional[datetime] = None
    destination_bank_line_id: Optional[UUID]
    confirmed_at: Optional[datetime]
    created_at: datetime


class Decision(BaseModel):
    action: Literal["approve", "reject"]
    reason: Optional[str] = Field(None, max_length=1000)


class ConfirmRequest(BaseModel):
    source_line_id: UUID
    destination_line_id: Optional[UUID] = None


class MatchJournal(BaseModel):
    journal_entry_id: UUID


class StatementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    bank_account_id: UUID
    statement_id: str
    row_number: int
    occurred_on: date
    amount_minor: int
    description: str
    reference: Optional[str]
    match_status: str
    journal_entry_id: Optional[UUID]
    request_id: Optional[UUID]


class TreasurySummary(BaseModel):
    banks: list[BankOut]
    pending_requests: int
    approved_awaiting_bank_evidence: int
    unmatched_statement_lines: int
    confirmed_expenses_minor: int


class BudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    kind: str
    id: UUID
    name: str
    category: str
    amount_minor: int
    starts_on: date
    ends_on: date


class LedgerAccountLink(BaseModel):
    account_id: UUID

class SnapshotCreate(BaseModel):
    as_of: date
    balance_minor: int
    evidence_reference: str = Field(min_length=5, max_length=250)


class ForecastCreate(BaseModel):
    direction: Literal["inflow", "outflow"]
    scenario: Literal["base", "optimistic", "conservative"] = "base"
    effective_on: date
    amount_minor: int = Field(gt=0)
    purpose: str = Field(min_length=5, max_length=400)
    programme: Optional[str] = Field(None, max_length=80)


class ForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    direction: str
    scenario: str
    effective_on: date
    amount_minor: int
    purpose: str
    programme: Optional[str] = None


class CashProjection(BaseModel):
    scenario: str
    latest_bank_snapshot_minor: Optional[int]
    latest_bank_snapshot_date: Optional[date]
    planned_inflows_minor: int
    planned_outflows_minor: int
    projected_ending_minor: Optional[int]
    pending_approved_expenses_minor: int
    caveat: str


class BankReconciliationRow(BaseModel):
    bank_id: UUID
    code: str
    latest_statement_balance_minor: Optional[int]
    latest_statement_date: Optional[date]
    ledger_balance_minor: Optional[int]
    difference_minor: Optional[int]
    matched_rows: int
    unmatched_rows: int
    status: str


class PostConfirmedRequest(BaseModel):
    expense_account_code: Optional[str] = None
    acknowledgement: bool = False
