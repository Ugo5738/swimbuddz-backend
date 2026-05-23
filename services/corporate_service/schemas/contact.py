"""Pydantic schemas for CorporateContact."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from services.corporate_service.models.enums import (
    CompanyIndustry,
    CompanySize,
    ContactSource,
)


class CorporateContactBase(BaseModel):
    company_name: str = Field(..., max_length=255)
    company_website: Optional[str] = Field(None, max_length=255)
    industry: Optional[CompanyIndustry] = None
    company_size: Optional[CompanySize] = None
    hq_location: Optional[str] = Field(None, max_length=255)

    primary_contact_name: str = Field(..., max_length=255)
    primary_contact_role: Optional[str] = Field(None, max_length=255)
    primary_contact_email: EmailStr
    primary_contact_phone: Optional[str] = Field(None, max_length=50)
    primary_contact_whatsapp: Optional[str] = Field(None, max_length=50)

    source: ContactSource = ContactSource.COLD_OUTBOUND
    owner_auth_id: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


class CorporateContactCreate(CorporateContactBase):
    pass


class CorporateContactUpdate(BaseModel):
    company_name: Optional[str] = Field(None, max_length=255)
    company_website: Optional[str] = Field(None, max_length=255)
    industry: Optional[CompanyIndustry] = None
    company_size: Optional[CompanySize] = None
    hq_location: Optional[str] = Field(None, max_length=255)

    primary_contact_name: Optional[str] = Field(None, max_length=255)
    primary_contact_role: Optional[str] = Field(None, max_length=255)
    primary_contact_email: Optional[EmailStr] = None
    primary_contact_phone: Optional[str] = Field(None, max_length=50)
    primary_contact_whatsapp: Optional[str] = Field(None, max_length=50)

    source: Optional[ContactSource] = None
    owner_auth_id: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None
    is_active: Optional[bool] = None


class CorporateContactResponse(CorporateContactBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CorporateContactListResponse(BaseModel):
    items: list[CorporateContactResponse]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Public lead capture (marketing site intake form)
# ---------------------------------------------------------------------------


class PublicLeadCreate(BaseModel):
    """Minimal-friction inbound lead from swimbuddz.com/corporate.

    Honeypot field ``website`` is included to catch naive bots — legitimate
    browsers leave it empty; bots that auto-fill all inputs trip the trap.
    The intake route rejects any submission where it's set.
    """

    company_name: str = Field(..., min_length=1, max_length=255)
    primary_contact_name: str = Field(..., min_length=1, max_length=255)
    primary_contact_email: EmailStr
    employee_count: Optional[int] = Field(None, ge=1, le=10_000)
    message: Optional[str] = Field(None, max_length=2000)

    # Anti-bot honeypot — must be empty / absent.
    website: Optional[str] = Field(None, max_length=255)


class PublicLeadResponse(BaseModel):
    """Returned to the marketing site after a successful submission.

    Intentionally opaque — we do not echo back the created contact id, just a
    confirmation. The admin sees the new contact in the pipeline view.
    """

    ok: bool = True
    message: str = (
        "Thanks — we've received your enquiry and will be in touch within "
        "one working day."
    )
