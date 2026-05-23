"""Pydantic schemas for CorporateProgramEmployee."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from services.corporate_service.models.enums import EmployeeEnrollmentStatus


class EmployeeRow(BaseModel):
    """A single row in a bulk-add request."""

    full_name: str = Field(..., max_length=255)
    email: EmailStr
    phone: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None


class EmployeeBulkAddRequest(BaseModel):
    """Bulk-add employees to a program.

    Idempotent on email — rows whose email already exists on the program are
    skipped (existing row unchanged). Returned counts let the caller report
    "added 7, skipped 1 duplicate" to the admin user.
    """

    employees: list[EmployeeRow] = Field(..., min_length=1, max_length=500)


class EmployeeBulkAddResponse(BaseModel):
    added: int
    skipped_duplicates: int
    items: list["CorporateProgramEmployeeResponse"]


class CorporateProgramEmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    program_id: uuid.UUID
    full_name: str
    email: str
    phone: Optional[str] = None
    member_id: Optional[uuid.UUID] = None
    member_auth_id: Optional[str] = None
    enrollment_status: EmployeeEnrollmentStatus
    invitation_sent_at: Optional[datetime] = None
    registered_at: Optional[datetime] = None
    enrolled_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


EmployeeBulkAddResponse.model_rebuild()


class MatchMembersResponse(BaseModel):
    """Result of resolving employee emails against members_service."""

    matched: int  # number of rows whose member_id was just set
    already_matched: int  # already had a member_id
    unresolved: int  # no member account with that email yet
