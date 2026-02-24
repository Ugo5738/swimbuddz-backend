"""Topup request/response schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from services.wallet_service.models.enums import PaymentMethod, TopupStatus


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


class ConfirmTopupRequest(BaseModel):
    """Called by payments service after Paystack webhook confirms payment."""

    topup_reference: str
    payment_reference: str
    # Accept both legacy "status" and explicit "payment_status".
    payment_status: str = Field(
        validation_alias=AliasChoices("payment_status", "status")
    )  # "completed" or "failed"
