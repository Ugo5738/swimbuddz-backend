"""Transaction request/response schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from services.wallet_service.models.enums import (
    TransactionDirection,
    TransactionStatus,
    TransactionType,
)


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


class InternalDebitCreditResponse(BaseModel):
    success: bool
    transaction_id: uuid.UUID
    balance_after: int
