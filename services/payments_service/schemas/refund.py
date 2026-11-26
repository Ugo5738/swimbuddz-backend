"""Pydantic v2 schemas for the shared Refund subsystem (ADR-024)."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.payments_service.models.enums import RefundMethod, RefundStatus


class RefundCreateRequest(BaseModel):
    """Internal service-to-service request to create a refund record."""

    idempotency_key: str = Field(..., min_length=4, max_length=160)
    payment_reference: Optional[str] = Field(default=None, max_length=128)
    member_auth_id: str = Field(..., min_length=1, max_length=255)
    payer_email: Optional[str] = Field(default=None, max_length=255)

    source_service: str = Field(..., min_length=2, max_length=40)
    source_entity_type: str = Field(..., min_length=2, max_length=64)
    source_entity_id: str = Field(..., min_length=1, max_length=128)
    purpose: str = Field(default="store_order", max_length=64)
    currency: str = Field(default="NGN", max_length=8)

    requested_total_kobo: int = Field(..., ge=0)
    cash_refund_kobo: int = Field(default=0, ge=0)
    bubbles_refund: int = Field(default=0, ge=0)
    store_credit_refund_kobo: int = Field(default=0, ge=0)

    preferred_method: Optional[RefundMethod] = None
    reason: Optional[str] = Field(default=None, max_length=1000)
    admin_note: Optional[str] = Field(default=None, max_length=1000)
    override_reason: Optional[str] = Field(default=None, max_length=1000)

    requested_by_auth_id: Optional[str] = None
    approved_by_auth_id: Optional[str] = None
    auto_approve: bool = False
    auto_initiate_paystack: bool = False

    recipient_bank_code: Optional[str] = Field(default=None, max_length=32)
    recipient_bank_name: Optional[str] = Field(default=None, max_length=128)
    recipient_account_number: Optional[str] = Field(default=None, max_length=32)
    recipient_account_name: Optional[str] = Field(default=None, max_length=255)

    wallet_refund_transaction_id: Optional[str] = None
    store_credit_id: Optional[str] = None
    metadata: Optional[dict] = None


class RefundApproveRequest(BaseModel):
    """Admin approves a requested refund."""

    admin_note: Optional[str] = Field(default=None, max_length=1000)
    override_reason: Optional[str] = Field(default=None, max_length=1000)
    auto_initiate_paystack: bool = False


class RefundInitiatePaystackRequest(BaseModel):
    """Admin initiates Paystack API refund for an approved refund."""

    customer_note: Optional[str] = Field(default=None, max_length=500)
    merchant_note: Optional[str] = Field(default=None, max_length=500)


class RefundVerifyBankRequest(BaseModel):
    """Verify recipient NUBAN via Paystack resolve_account before manual refund."""

    bank_code: str = Field(..., min_length=2, max_length=32)
    bank_name: Optional[str] = Field(default=None, max_length=128)
    account_number: str = Field(..., min_length=10, max_length=10)


class RefundCompleteManualRequest(BaseModel):
    """Admin confirms manual bank-transfer refund disbursement with bank reference."""

    external_bank_reference: str = Field(..., min_length=3, max_length=128)
    admin_note: Optional[str] = Field(default=None, max_length=1000)


class RefundFailOrCancelRequest(BaseModel):
    """Admin marks a refund failed or cancels an uninitiated request."""

    action: str = Field(..., pattern="^(cancel|fail)$")
    reason: str = Field(..., min_length=3, max_length=1000)


class RefundResponse(BaseModel):
    """Canonical refund representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    refund_reference: str
    idempotency_key: str
    payment_id: Optional[uuid.UUID] = None
    payment_reference: Optional[str] = None
    member_auth_id: str
    payer_email: Optional[str] = None

    source_service: str
    source_entity_type: str
    source_entity_id: str
    purpose: str
    currency: str

    requested_total_kobo: int
    cash_refund_kobo: int
    bubbles_refund: int
    store_credit_refund_kobo: int

    refund_method: RefundMethod
    status: RefundStatus
    reason: Optional[str] = None
    admin_note: Optional[str] = None
    override_reason: Optional[str] = None

    provider: Optional[str] = None
    provider_refund_id: Optional[str] = None
    provider_status: Optional[str] = None
    external_bank_reference: Optional[str] = None

    recipient_bank_code: Optional[str] = None
    recipient_bank_name: Optional[str] = None
    recipient_account_number: Optional[str] = None
    recipient_account_name: Optional[str] = None
    recipient_account_verified: bool = False

    wallet_refund_transaction_id: Optional[str] = None
    store_credit_id: Optional[str] = None

    ledger_accrual_posted: bool = False
    ledger_disbursement_posted: bool = False

    requested_by_auth_id: Optional[str] = None
    approved_by_auth_id: Optional[str] = None
    approved_at: Optional[datetime] = None
    initiated_by_auth_id: Optional[str] = None
    initiated_at: Optional[datetime] = None
    completed_by_auth_id: Optional[str] = None
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class RefundListResponse(BaseModel):
    """Paginated admin refund queue with summary totals."""

    items: list[RefundResponse]
    total: int
    page: int
    page_size: int
    pending_cash_kobo: int = 0
    initiated_cash_kobo: int = 0
    completed_cash_kobo: int = 0


class PaymentReferenceStatusResponse(BaseModel):
    """Internal payment state check used for safe unpaid cancellation & refunds."""

    exists: bool
    reference: str
    status: Optional[str] = None
    payment_method: Optional[str] = None
    provider: Optional[str] = None
    amount_kobo: int = 0
    paid_at: Optional[datetime] = None
    has_proof: bool = False
    refunded_cash_kobo: int = 0
    refundable_cash_kobo: int = 0
    can_self_cancel_unpaid: bool = True
    block_reason: Optional[str] = None


class CloseUnpaidStorePaymentResponse(BaseModel):
    """Result of attempting to close an unpaid store payment intent under lock."""

    closed: bool
    reference: str
    status: str
    detail: str
