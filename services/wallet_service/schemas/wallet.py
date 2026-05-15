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
    referral_code: Optional[str] = None


class WalletEcosystemStatsResponse(BaseModel):
    """Aggregate wallet ecosystem stats over a date window.

    Consumed by ``reporting_service.tasks.flywheel._fetch_wallet_ecosystem_aggregates``.
    """

    active_wallet_users: int = 0
    single_service_users: int = 0
    cross_service_users: int = 0
    total_bubbles_spent: int = 0
    total_topup_bubbles: int = 0
    spend_distribution: dict[str, float] = {}


class MemberWalletSummary(BaseModel):
    """Aggregate wallet activity for one member in a date window.

    Consumed by ``reporting_service`` when building quarterly member reports.
    """

    bubbles_earned: int = 0
    bubbles_spent: int = 0
