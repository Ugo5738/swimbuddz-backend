"""Pydantic schemas for the HR-facing /corporate/me/* portal endpoints."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from services.corporate_service.models.enums import ProgramStatus


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RequestMagicLinkRequest(BaseModel):
    """Body for POST /corporate/me/auth/request-link."""

    email: EmailStr
    # Where the magic-link callback page lives — usually the frontend's
    # /corporate-portal/verify route. We accept it from the client rather
    # than baking it in so the same backend can serve preview / staging /
    # prod without redeploys.
    callback_url: str = Field(..., max_length=2048)


class RequestMagicLinkResponse(BaseModel):
    """Returned generically — always {sent: true} so we don't leak which
    emails are on file. The email lands only if a matching active contact
    exists. Identical-looking responses kill email-enumeration attacks."""

    sent: bool = True


class VerifyMagicLinkRequest(BaseModel):
    """Body for POST /corporate/me/auth/verify — sent by the frontend
    once it has lifted the ``token`` query param off the magic link."""

    token: str


class VerifyMagicLinkResponse(BaseModel):
    session_token: str
    expires_at: datetime
    contact_id: uuid.UUID
    company_name: str
    primary_contact_name: str


# ---------------------------------------------------------------------------
# Read-only views of programs / employees the caller is allowed to see
# ---------------------------------------------------------------------------


class PortalProgramSummary(BaseModel):
    """Slim view of a CorporateProgram for the portal dashboard."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: ProgramStatus
    employee_count: int
    expected_start_date: Optional[str] = None
    expected_end_date: Optional[str] = None
    actual_start_date: Optional[str] = None
    actual_end_date: Optional[str] = None


class PortalEmployeeRow(BaseModel):
    """Single employee on the HR-side manifest — read-only."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str
    enrollment_status: str
    invitation_sent_at: Optional[datetime] = None
    registered_at: Optional[datetime] = None
    enrolled_at: Optional[datetime] = None
