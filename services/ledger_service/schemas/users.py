"""Schemas for finance-team user management (P1.6b)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, model_validator
from services.ledger_service.models.enums import LedgerRole


class LedgerUserCreate(BaseModel):
    """Register a finance-team member by email and/or auth_id."""

    role: LedgerRole
    email: Optional[EmailStr] = None
    auth_id: Optional[str] = None

    @model_validator(mode="after")
    def _need_identifier(self) -> "LedgerUserCreate":
        if not self.email and not self.auth_id:
            raise ValueError("provide email or auth_id")
        return self


class LedgerUserUpdate(BaseModel):
    role: LedgerRole


class LedgerUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    auth_id: Optional[str] = None
    email: Optional[str] = None
    role: LedgerRole
    created_at: datetime
    deactivated_at: Optional[datetime] = None

    # Set only on add / resend-invite responses — not a stored column. One of
    # "invited" (email sent), "exists" (already had a login), "error" (send
    # failed — fall back to a manual Supabase invite). None on list/role-change.
    invite_status: Optional[str] = None
