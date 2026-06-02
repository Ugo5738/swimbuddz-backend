"""Invoice schemas (R5-PR1, design §13)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class InvoiceLineIn(BaseModel):
    description: str = Field(..., min_length=1)
    quantity: int = Field(1, ge=1)
    unit_price_minor: int = Field(..., ge=0)
    # If omitted, computed as quantity * unit_price_minor.
    amount_minor: Optional[int] = Field(None, ge=0)
    dimension_1: Optional[str] = None


class InvoiceCreate(BaseModel):
    org_id: Optional[uuid.UUID] = Field(
        None, description="Phase 1: ignored; server uses LEDGER_DEFAULT_ORG_ID"
    )
    source_service: Optional[str] = None
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    customer_ref: Optional[str] = None
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_tin: Optional[str] = None
    currency: str = "NGN"
    issue_date: Optional[date] = None  # defaults to today (server-side)
    due_date: Optional[date] = None
    notes: Optional[str] = None
    status: str = "issued"  # draft | issued
    prefix: str = "SB"
    metadata: Optional[dict] = None
    lines: list[InvoiceLineIn] = Field(..., min_length=1)


class InvoiceLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    description: str
    quantity: int
    unit_price_minor: int
    amount_minor: int
    tax_code_ref: Optional[str] = None
    tax_minor: int
    dimension_1: Optional[str] = None


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_number: str
    status: str
    source_service: Optional[str] = None
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    customer_ref: Optional[str] = None
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_tin: Optional[str] = None
    currency: str
    subtotal_minor: int
    tax_minor: int
    total_minor: int
    issue_date: date
    due_date: Optional[date] = None
    notes: Optional[str] = None
    irn: Optional[str] = None
    firs_status: Optional[str] = None
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None
    created_at: datetime
    lines: list[InvoiceLineOut] = []


class InvoiceListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_number: str
    status: str
    customer_name: Optional[str] = None
    currency: str
    total_minor: int
    issue_date: date
    created_at: datetime


class InvoiceList(BaseModel):
    items: list[InvoiceListItem]
    total: int


class InvoiceVoidRequest(BaseModel):
    reason: Optional[str] = None
