"""Admin-specific schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from services.wallet_service.models.enums import AuditAction
from services.wallet_service.schemas.topup import TopupResponse
from services.wallet_service.schemas.transaction import TransactionResponse
from services.wallet_service.schemas.wallet import WalletResponse


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
