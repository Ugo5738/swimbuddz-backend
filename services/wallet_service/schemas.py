"""Pydantic request/response schemas for the Wallet Service."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from services.wallet_service.models import (
    AuditAction,
    GrantType,
    PaymentMethod,
    TopupStatus,
    TransactionDirection,
    TransactionStatus,
    TransactionType,
    WalletStatus,
    WalletTier,
)

# ---------------------------------------------------------------------------
# Wallet Schemas
# ---------------------------------------------------------------------------


class WalletResponse(BaseModel):
    id: uuid.UUID
    member_id: uuid.UUID
    member_auth_id: str
    balance: int
    lifetime_bubbles_purchased: int
    lifetime_bubbles_spent: int
    lifetime_bubbles_received: int
    status: WalletStatus
    frozen_reason: Optional[str] = None
    frozen_at: Optional[datetime] = None
    wallet_tier: WalletTier
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WalletCreateRequest(BaseModel):
    """Used by members service to create wallet on registration."""

    member_id: uuid.UUID
    member_auth_id: str


# ---------------------------------------------------------------------------
# Transaction Schemas
# ---------------------------------------------------------------------------


class TransactionResponse(BaseModel):
    id: uuid.UUID
    wallet_id: uuid.UUID
    idempotency_key: str
    transaction_type: TransactionType
    direction: TransactionDirection
    amount: int
    balance_before: int
    balance_after: int
    status: TransactionStatus
    description: str
    service_source: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    initiated_by: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TransactionListResponse(BaseModel):
    transactions: list[TransactionResponse]
    total: int
    skip: int
    limit: int


# ---------------------------------------------------------------------------
# Topup Schemas
# ---------------------------------------------------------------------------


class TopupInitiateRequest(BaseModel):
    bubbles_amount: int = Field(
        ..., ge=25, le=5000, description="Bubbles to purchase (25–5,000)"
    )
    payment_method: PaymentMethod = PaymentMethod.PAYSTACK
    callback_url: Optional[str] = Field(
        None,
        description="Frontend path to redirect to after payment (e.g. /coach/wallet). Defaults to /account/wallet.",
    )


class TopupResponse(BaseModel):
    id: uuid.UUID
    wallet_id: uuid.UUID
    member_auth_id: str
    reference: str
    bubbles_amount: int
    naira_amount: int
    exchange_rate: int
    payment_reference: Optional[str] = None
    payment_method: PaymentMethod
    status: TopupStatus
    paystack_authorization_url: Optional[str] = None
    paystack_access_code: Optional[str] = None
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TopupListResponse(BaseModel):
    topups: list[TopupResponse]
    total: int
    skip: int
    limit: int


# ---------------------------------------------------------------------------
# Internal / Service-to-Service Schemas
# ---------------------------------------------------------------------------


class DebitRequest(BaseModel):
    """Request from another service to debit a member's wallet."""

    idempotency_key: str
    member_auth_id: str
    amount: int = Field(..., gt=0)
    transaction_type: TransactionType = TransactionType.PURCHASE
    description: str
    service_source: str
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None


class CreditRequest(BaseModel):
    """Request from another service to credit a member's wallet."""

    idempotency_key: str
    member_auth_id: str
    amount: int = Field(..., gt=0)
    transaction_type: TransactionType = TransactionType.REFUND
    description: str
    service_source: str
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None


class BalanceCheckRequest(BaseModel):
    member_auth_id: str
    required_amount: int = Field(..., gt=0)


class BalanceCheckResponse(BaseModel):
    sufficient: bool
    current_balance: int
    required_amount: int
    wallet_status: WalletStatus


class BalanceResponse(BaseModel):
    wallet_id: uuid.UUID
    member_auth_id: str
    balance: int
    status: WalletStatus


class ConfirmTopupRequest(BaseModel):
    """Called by payments service after Paystack webhook confirms payment."""

    topup_reference: str
    payment_reference: str
    # Accept both legacy "status" and explicit "payment_status".
    payment_status: str = Field(
        validation_alias=AliasChoices("payment_status", "status")
    )  # "completed" or "failed"


class InternalDebitCreditResponse(BaseModel):
    success: bool
    transaction_id: uuid.UUID
    balance_after: int


class GrantWelcomeBonusRequest(BaseModel):
    """Called by members service after paid membership activation."""

    member_id: uuid.UUID
    member_auth_id: str
    eligible: bool = True
    reason: Optional[str] = None
    granted_by: str = "system"


class GrantWelcomeBonusResponse(BaseModel):
    success: bool
    wallet_id: uuid.UUID
    bonus_granted: bool
    bubbles_awarded: int


# ---------------------------------------------------------------------------
# Admin Internal Schemas
# ---------------------------------------------------------------------------


class AdminScholarshipCreditRequest(BaseModel):
    """Admin-initiated wallet credit for scholarship or discount deposits.

    Called by academy_service admin endpoints (or directly by admin tooling)
    to deposit Bubbles that cover part or all of a student's installment fees.
    The credit is applied immediately and the idempotency_key ensures that
    retries do not double-credit the same grant.
    """

    member_auth_id: str
    amount: int = Field(..., gt=0, description="Amount in Bubbles (kobo-unit integers)")
    idempotency_key: str = Field(
        ...,
        description=(
            "Unique key for this credit operation. "
            "Recommended format: scholarship-{enrollment_id}-{reason_slug}"
        ),
    )
    reason: str = Field(
        ..., min_length=3, description="Reason for credit (e.g. 'scholarship_50pct')"
    )
    grant_type: GrantType = GrantType.SCHOLARSHIP
    enrollment_id: Optional[str] = None  # For audit trail linking


# ---------------------------------------------------------------------------
# Admin Schemas
# ---------------------------------------------------------------------------


class MemberIdentityResponse(BaseModel):
    member_id: Optional[str] = None
    member_auth_id: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None


class AdminWalletResponse(WalletResponse):
    member: Optional[MemberIdentityResponse] = None


class AdminWalletListResponse(BaseModel):
    wallets: list[AdminWalletResponse]
    total: int
    skip: int
    limit: int


class FreezeWalletRequest(BaseModel):
    reason: str = Field(..., min_length=5, description="Reason for freezing the wallet")


class UnfreezeWalletRequest(BaseModel):
    reason: str = Field(
        default="Admin unfroze wallet",
        description="Reason for unfreezing",
    )


class AdjustBalanceRequest(BaseModel):
    amount: int = Field(..., description="Positive to credit, negative to debit")
    reason: str = Field(..., min_length=5)


class GrantPromotionalRequest(BaseModel):
    member_auth_id: str
    bubbles_amount: int = Field(..., gt=0)
    grant_type: GrantType
    reason: str = Field(..., min_length=5)
    campaign_code: Optional[str] = None
    expires_in_days: Optional[int] = Field(
        default=None,
        description="Days until expiry. Null = never expires.",
    )


class BulkGrantPromotionalRequest(BaseModel):
    member_auth_ids: list[str]
    bubbles_amount: int = Field(..., gt=0)
    grant_type: GrantType
    reason: str = Field(..., min_length=5)
    campaign_code: Optional[str] = None
    expires_in_days: Optional[int] = None


class GrantResponse(BaseModel):
    id: uuid.UUID
    wallet_id: uuid.UUID
    member_auth_id: str
    grant_type: GrantType
    bubbles_amount: int
    reason: str
    campaign_code: Optional[str] = None
    expires_at: Optional[datetime] = None
    bubbles_remaining: int
    transaction_id: Optional[uuid.UUID] = None
    granted_by: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GrantListResponse(BaseModel):
    grants: list[GrantResponse]
    total: int
    skip: int
    limit: int


class AdminStatsResponse(BaseModel):
    total_wallets: int
    active_wallets: int
    frozen_wallets: int
    total_bubbles_in_circulation: int
    total_bubbles_spent_this_month: int
    total_topup_revenue_naira_this_month: int


class AdminTransactionListResponse(BaseModel):
    transactions: list[TransactionResponse]
    total: int
    skip: int
    limit: int


class AdminTopupResponse(TopupResponse):
    member: Optional[MemberIdentityResponse] = None


class AdminTopupListResponse(BaseModel):
    topups: list[AdminTopupResponse]
    total: int
    skip: int
    limit: int


class AuditLogEntry(BaseModel):
    id: uuid.UUID
    wallet_id: uuid.UUID
    action: AuditAction
    performed_by: str
    old_value: Optional[dict] = None
    new_value: Optional[dict] = None
    reason: str
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogListResponse(BaseModel):
    entries: list[AuditLogEntry]
    total: int
    skip: int
    limit: int
