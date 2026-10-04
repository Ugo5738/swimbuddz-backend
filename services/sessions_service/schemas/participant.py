"""SessionParticipant request/response schemas."""

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AdminGuestWalkInCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    email: Optional[str] = Field(default=None, max_length=320)
    phone: Optional[str] = Field(default=None, max_length=32)
    fee_amount_kobo: Optional[int] = Field(default=None, ge=0)
    payment_status: Optional[
        Literal["included", "unpaid", "paid", "waived", "unknown"]
    ] = None
    waiver_status: Literal["accepted", "missing", "not_required", "unknown"] = "missing"
    notes: Optional[str] = Field(default=None, max_length=500)


class SessionParticipantResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    participant_kind: str
    source: str
    member_id: Optional[uuid.UUID] = None
    booking_id: Optional[uuid.UUID] = None
    booking_guest_id: Optional[uuid.UUID] = None
    guest_pass_id: Optional[uuid.UUID] = None
    converted_member_id: Optional[uuid.UUID] = None
    full_name_snapshot: str
    email_snapshot: Optional[str] = None
    phone_snapshot: Optional[str] = None
    audience: Optional[str] = None
    access_source: Optional[str] = None
    rate_id: Optional[uuid.UUID] = None
    rate_code: Optional[str] = None
    fee_amount_kobo: int
    payment_status: str
    waiver_status: str
    notes: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdminGuestWalkInResponse(SessionParticipantResponse):
    attendance_recorded: bool = False
