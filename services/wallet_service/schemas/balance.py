"""Balance check schemas."""

import uuid

from pydantic import BaseModel, Field
from services.wallet_service.models.enums import WalletStatus


class BalanceCheckRequest(BaseModel):
    member_auth_id: str
    required_amount: int = Field(..., gt=0)


class BalanceCheckResponse(BaseModel):
    sufficient: bool
    current_balance: int
    ledger_balance: int | None = None
    held_balance: int = 0
    required_amount: int
    wallet_status: WalletStatus


class BalanceResponse(BaseModel):
    wallet_id: uuid.UUID
    member_auth_id: str
    balance: int
    available_balance: int
    held_balance: int
    status: WalletStatus
