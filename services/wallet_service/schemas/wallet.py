"""Wallet request/response schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict
from services.wallet_service.models.enums import WalletStatus, WalletTier


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
