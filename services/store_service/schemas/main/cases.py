"""Pydantic v2 schemas for Store post-purchase cases and order actions (ADR-024)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.store_service.models.enums import (
    OrderCaseResolution,
    OrderCaseStatus,
    OrderCaseType,
    ReturnInspectionCondition,
)


class OrderCaseItemInput(BaseModel):
    """Order line item reference when submitting a return, exchange, or defect claim."""

    order_item_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=50)
    replacement_variant_id: Optional[uuid.UUID] = None


class OrderCaseItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_item_id: uuid.UUID
    quantity: int
    replacement_variant_id: Optional[uuid.UUID] = None
    product_name: Optional[str] = None
    variant_name: Optional[str] = None
    sku: Optional[str] = None
    unit_price_ngn: Optional[Decimal] = None


class OrderCaseCreateRequest(BaseModel):
    """Customer submission for a cancellation request, return/exchange, or defect claim."""

    case_type: OrderCaseType
    reason_code: str = Field(..., min_length=2, max_length=64)
    customer_notes: Optional[str] = Field(default=None, max_length=1500)
    evidence_media_ids: list[str] = Field(default_factory=list, max_length=6)
    preferred_resolution: Optional[OrderCaseResolution] = None
    requested_variant_id: Optional[uuid.UUID] = None
    items: list[OrderCaseItemInput] = Field(default_factory=list)

    # Optional recipient bank details for manual bank-transfer refund preference
    recipient_bank_code: Optional[str] = Field(default=None, max_length=32)
    recipient_bank_name: Optional[str] = Field(default=None, max_length=128)
    recipient_account_number: Optional[str] = Field(default=None, max_length=32)
    recipient_account_name: Optional[str] = Field(default=None, max_length=255)


class OrderSelfCancelRequest(BaseModel):
    """Customer self-cancellation payload for unpaid or pre-prep stocked orders."""

    reason_code: str = Field(default="wrong_item_ordered", min_length=2, max_length=64)
    customer_notes: Optional[str] = Field(default=None, max_length=1000)
    recipient_bank_code: Optional[str] = Field(default=None, max_length=32)
    recipient_bank_name: Optional[str] = Field(default=None, max_length=128)
    recipient_account_number: Optional[str] = Field(default=None, max_length=32)
    recipient_account_name: Optional[str] = Field(default=None, max_length=255)


class OrderVariantSwapRequest(BaseModel):
    """Customer same-price variant swap before preparation."""

    order_item_id: uuid.UUID
    new_variant_id: uuid.UUID
    customer_notes: Optional[str] = Field(default=None, max_length=500)


class AdminCaseReviewRequest(BaseModel):
    """Admin approves or declines a post-purchase case."""

    decision: Literal["approve", "decline"]
    approved_resolution: Optional[OrderCaseResolution] = None
    require_return_before_resolution: bool = False
    include_delivery_fee: bool = False
    auto_initiate_paystack: bool = True
    is_policy_override: bool = False
    override_reason: Optional[str] = Field(default=None, max_length=1000)
    admin_notes: Optional[str] = Field(default=None, max_length=1500)


class AdminReceiveReturnRequest(BaseModel):
    """Admin records receipt & physical inspection of returned goods."""

    return_condition: ReturnInspectionCondition
    admin_notes: Optional[str] = Field(default=None, max_length=1500)
    include_delivery_fee: bool = False
    auto_initiate_paystack: bool = True
    override_resolution: Optional[OrderCaseResolution] = None


class AdminCancelAndRefundRequest(BaseModel):
    """Admin audited cancellation + refund initiation for a paid (or unpaid) order."""

    reason: str = Field(..., min_length=3, max_length=1000)
    resolution: OrderCaseResolution = OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD
    include_delivery_fee: bool = True
    auto_initiate_paystack: bool = True
    is_policy_override: bool = False
    override_reason: Optional[str] = Field(default=None, max_length=1000)


class OrderCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    case_number: str
    order_id: uuid.UUID
    order_number: Optional[str] = None
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    member_auth_id: Optional[str] = None
    case_type: OrderCaseType
    status: OrderCaseStatus
    reason_code: str
    customer_notes: Optional[str] = None
    evidence_media_ids: list[str] = Field(default_factory=list)
    preferred_resolution: Optional[OrderCaseResolution] = None
    approved_resolution: Optional[OrderCaseResolution] = None
    requested_variant_id: Optional[uuid.UUID] = None

    approved_refund_cash_ngn: Decimal = Decimal("0")
    approved_refund_bubbles: int = 0
    approved_refund_store_credit_ngn: Decimal = Decimal("0")
    delivery_fee_refunded: bool = False

    refund_reference: Optional[str] = None
    refund_status: Optional[str] = None

    recipient_bank_code: Optional[str] = None
    recipient_bank_name: Optional[str] = None
    recipient_account_number: Optional[str] = None
    recipient_account_name: Optional[str] = None

    return_condition: ReturnInspectionCondition = ReturnInspectionCondition.PENDING
    return_received_at: Optional[datetime] = None
    restocked_at: Optional[datetime] = None

    is_policy_override: bool = False
    override_reason: Optional[str] = None
    admin_notes: Optional[str] = None
    reviewed_by_auth_id: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    resolved_by_auth_id: Optional[str] = None
    resolved_at: Optional[datetime] = None

    created_at: datetime
    updated_at: datetime
    items: list[OrderCaseItemResponse] = Field(default_factory=list)


class OrderCaseListResponse(BaseModel):
    items: list[OrderCaseResponse]
    total: int
    page: int
    page_size: int


class OrderRefundSummaryItem(BaseModel):
    """Live refund status snapshot fetched from payments_service."""

    refund_reference: str
    status: str
    refund_method: str
    provider: Optional[str] = None
    cash_refund_kobo: int = 0
    bubbles_refund: int = 0
    store_credit_refund_kobo: int = 0
    provider_refund_id: Optional[str] = None
    external_bank_reference: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class OrderRefundPreview(BaseModel):
    """Exact tender preview shown to customer/admin before cancellation."""

    cash_refund_ngn: Decimal = Decimal("0")
    bubbles_refund: int = 0
    store_credit_refund_ngn: Decimal = Decimal("0")
    total_refund_value_ngn: Decimal = Decimal("0")
    delivery_fee_included: bool = True
    payment_method: Optional[str] = None


class OrderActionEligibilityResponse(BaseModel):
    """Server-computed post-purchase actions available for an order (ADR-024)."""

    order_number: str
    payment_status: str
    fulfillment_status: str
    can_self_cancel_unpaid: bool = False
    can_self_cancel_paid: bool = False
    can_request_cancel: bool = False
    can_swap_variant: bool = False
    can_request_return_exchange: bool = False
    can_report_defect: bool = False

    has_preorder_or_dropship: bool = False
    has_swimwear_items: bool = False
    has_open_case: bool = False
    payment_pending_review: bool = False

    return_window_ends_at: Optional[datetime] = None
    cancel_block_reason: Optional[str] = None
    policy_notes: list[str] = Field(default_factory=list)
    full_cancel_refund_preview: OrderRefundPreview = Field(
        default_factory=OrderRefundPreview
    )
    cases: list[OrderCaseResponse] = Field(default_factory=list)
    refunds: list[OrderRefundSummaryItem] = Field(default_factory=list)


class OrderCancelResultResponse(BaseModel):
    order_number: str
    status: str
    payment_status: str
    fulfillment_status: str
    case: Optional[OrderCaseResponse] = None
    refund_reference: Optional[str] = None
    refund_status: Optional[str] = None
    message: str


class StoreRefundStatusCallback(BaseModel):
    """Internal callback sent by payments_service when a refund changes state."""

    refund_reference: str
    source_entity_type: str
    source_entity_id: str
    payment_reference: Optional[str] = None
    status: str
    cash_refund_kobo: int = 0
    bubbles_refund: int = 0
    store_credit_refund_kobo: int = 0
    provider: Optional[str] = None
    provider_refund_id: Optional[str] = None
    external_bank_reference: Optional[str] = None
    failure_reason: Optional[str] = None
    completed_at: Optional[datetime] = None
