"""Store credit schemas."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.store_service.models import StoreCreditSourceType


class StoreCreditCreate(BaseModel):
    """Issue store credit (admin)."""

    member_auth_id: str
    amount_ngn: Decimal = Field(..., gt=0)
    source_type: StoreCreditSourceType = StoreCreditSourceType.ADMIN
    source_order_id: Optional[uuid.UUID] = None
    reason: Optional[str] = None
    expires_at: Optional[datetime] = None


class StoreCreditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_auth_id: str
    amount_ngn: Decimal
    balance_ngn: Decimal
    source_type: StoreCreditSourceType
    source_order_id: Optional[uuid.UUID]
    reason: Optional[str]
    expires_at: Optional[datetime]
    issued_by: Optional[str]
    created_at: datetime


class MemberStoreCreditSummary(BaseModel):
    """Summary of member's store credits."""

    total_balance_ngn: Decimal
    credits: list[StoreCreditResponse]
