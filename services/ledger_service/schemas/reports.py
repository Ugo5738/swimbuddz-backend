"""Read/report schemas for the admin finance surface."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    type: str
    normal_balance: str
    is_active: bool
    is_system: bool


class JournalLineOut(BaseModel):
    account_id: uuid.UUID
    account_code: Optional[str] = None
    debit_minor: int
    credit_minor: int
    currency: str
    cost_center_id: Optional[uuid.UUID] = None
    dimension_1: Optional[str] = None
    dimension_2: Optional[str] = None
    member_ref: Optional[str] = None
    external_ref: Optional[str] = None
    description: Optional[str] = None


class JournalEntrySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entry_date: date
    posting_date: datetime
    description: str
    source_service: str
    source_type: str
    source_id: Optional[str] = None
    status: str
    period_id: uuid.UUID
    reversal_of_entry_id: Optional[uuid.UUID] = None
    reversed_by_entry_id: Optional[uuid.UUID] = None


class JournalEntryDetail(JournalEntrySummary):
    lines: list[JournalLineOut] = []


class TrialBalanceRow(BaseModel):
    code: str
    name: str
    type: str
    debit_minor: int
    credit_minor: int


class TrialBalanceReport(BaseModel):
    as_of: date
    rows: list[TrialBalanceRow]
    total_debit_minor: int
    total_credit_minor: int
    balanced: bool


class ProfitLossRow(BaseModel):
    key: str
    revenue_minor: int
    expense_minor: int
    net_minor: int


class ProfitLossReport(BaseModel):
    from_date: date
    to_date: date
    group_by: str
    rows: list[ProfitLossRow]
    total_revenue_minor: int
    total_expense_minor: int
    net_income_minor: int


class DeferredRevenueRow(BaseModel):
    deferred_account_ref: str
    domain: str
    schedule_count: int
    total_minor: int
    recognized_minor: int
    remaining_minor: int


class DeferredRevenueReport(BaseModel):
    as_of: date
    rows: list[DeferredRevenueRow]
    total_remaining_minor: int


class PeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    period_name: str
    period_type: str
    start_date: date
    end_date: date
    status: str
    closed_at: Optional[datetime] = None


class PeriodTransitionRequest(BaseModel):
    to_status: str  # "open" | "soft_closed" | "hard_closed"
