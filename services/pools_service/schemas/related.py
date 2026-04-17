"""Pydantic schemas for pool-related entities (contacts, visits, status changes,
agreements, assets)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from services.pools_service.models.enums import (
    PartnershipStatus,
    PoolAgreementStatus,
    PoolAssetType,
    PoolContactRole,
    PoolVisitType,
)

# ═════════════════════════════════════════════════════════════════════════
# POOL CONTACTS
# ═════════════════════════════════════════════════════════════════════════


class PoolContactBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    role: PoolContactRole = PoolContactRole.MANAGER
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[EmailStr] = None
    whatsapp: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = Field(None, max_length=4000)
    is_primary: bool = False


class PoolContactCreate(PoolContactBase):
    pass


class PoolContactUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    role: Optional[PoolContactRole] = None
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[EmailStr] = None
    whatsapp: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = Field(None, max_length=4000)
    is_primary: Optional[bool] = None


class PoolContactResponse(PoolContactBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pool_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ═════════════════════════════════════════════════════════════════════════
# POOL VISITS
# ═════════════════════════════════════════════════════════════════════════


class PoolVisitBase(BaseModel):
    visit_date: date
    visit_type: PoolVisitType = PoolVisitType.SCOUTING
    summary: str = Field(..., min_length=1, max_length=500)
    notes: Optional[str] = Field(None, max_length=8000)
    follow_up_action: Optional[str] = Field(None, max_length=4000)
    follow_up_due_at: Optional[date] = None
    follow_up_completed: bool = False


class PoolVisitCreate(PoolVisitBase):
    pass


class PoolVisitUpdate(BaseModel):
    visit_date: Optional[date] = None
    visit_type: Optional[PoolVisitType] = None
    summary: Optional[str] = Field(None, min_length=1, max_length=500)
    notes: Optional[str] = Field(None, max_length=8000)
    follow_up_action: Optional[str] = Field(None, max_length=4000)
    follow_up_due_at: Optional[date] = None
    follow_up_completed: Optional[bool] = None


class PoolVisitResponse(PoolVisitBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pool_id: uuid.UUID
    visitor_auth_id: Optional[str]
    visitor_display_name: Optional[str]
    created_at: datetime
    updated_at: datetime


# ═════════════════════════════════════════════════════════════════════════
# POOL STATUS CHANGES (read-only, auto-created)
# ═════════════════════════════════════════════════════════════════════════


class PoolStatusChangeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pool_id: uuid.UUID
    from_status: Optional[PartnershipStatus]
    to_status: PartnershipStatus
    changed_by_auth_id: Optional[str]
    reason: Optional[str]
    created_at: datetime


# ═════════════════════════════════════════════════════════════════════════
# POOL AGREEMENTS
# ═════════════════════════════════════════════════════════════════════════


class PoolAgreementBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    status: PoolAgreementStatus = PoolAgreementStatus.DRAFT
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    signed_at: Optional[datetime] = None
    commission_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    flat_session_rate_ngn: Optional[Decimal] = Field(None, ge=0)
    min_sessions_per_month: Optional[int] = Field(None, ge=0)
    is_exclusive: bool = False
    signed_doc_media_id: Optional[uuid.UUID] = None
    signed_doc_url: Optional[str] = Field(None, max_length=2000)
    notes: Optional[str] = Field(None, max_length=8000)


class PoolAgreementCreate(PoolAgreementBase):
    pass


class PoolAgreementUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    status: Optional[PoolAgreementStatus] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    signed_at: Optional[datetime] = None
    commission_percentage: Optional[Decimal] = Field(None, ge=0, le=100)
    flat_session_rate_ngn: Optional[Decimal] = Field(None, ge=0)
    min_sessions_per_month: Optional[int] = Field(None, ge=0)
    is_exclusive: Optional[bool] = None
    signed_doc_media_id: Optional[uuid.UUID] = None
    signed_doc_url: Optional[str] = Field(None, max_length=2000)
    notes: Optional[str] = Field(None, max_length=8000)


class PoolAgreementResponse(PoolAgreementBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pool_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ═════════════════════════════════════════════════════════════════════════
# POOL ASSETS
# ═════════════════════════════════════════════════════════════════════════


class PoolAssetBase(BaseModel):
    asset_type: PoolAssetType = PoolAssetType.PHOTO
    media_id: Optional[uuid.UUID] = None
    url: Optional[str] = Field(None, max_length=2000)
    title: Optional[str] = Field(None, max_length=255)
    caption: Optional[str] = Field(None, max_length=2000)
    display_order: int = 0
    is_primary: bool = False


class PoolAssetCreate(PoolAssetBase):
    pass


class PoolAssetUpdate(BaseModel):
    asset_type: Optional[PoolAssetType] = None
    media_id: Optional[uuid.UUID] = None
    url: Optional[str] = Field(None, max_length=2000)
    title: Optional[str] = Field(None, max_length=255)
    caption: Optional[str] = Field(None, max_length=2000)
    display_order: Optional[int] = None
    is_primary: Optional[bool] = None


class PoolAssetResponse(PoolAssetBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pool_id: uuid.UUID
    uploaded_by_auth_id: Optional[str]
    created_at: datetime
    updated_at: datetime
