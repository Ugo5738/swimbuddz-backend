"""Internal API schemas for wallet holds."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from services.wallet_service.models.enums import WalletHoldStatus


class WalletHoldCreateRequest(BaseModel):
    member_auth_id: str
    amount: int = Field(..., gt=0)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    service_source: str = Field(..., min_length=1)
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    expires_in_seconds: int = Field(default=1800, ge=60, le=86400)


class WalletHoldResponse(BaseModel):
    id: uuid.UUID
    wallet_id: uuid.UUID
    member_auth_id: str
    idempotency_key: str
    amount: int
    status: WalletHoldStatus
    description: str
    service_source: str
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    wallet_transaction_id: Optional[uuid.UUID] = None
    expires_at: datetime
    captured_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    available_balance: int

    model_config = ConfigDict(from_attributes=True)
