import uuid
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field, model_validator


class AdminGuestWalkInCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=160)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, min_length=7, max_length=32)
    fee_amount_kobo: Optional[int] = Field(default=None, ge=0)
    fee_override_reason: Optional[str] = Field(default=None, max_length=500)
    payment_status: Literal["unreconciled", "pending", "not_due"] = "unreconciled"
    notes: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def normalize(self):
        self.full_name = self.full_name.strip()
        self.phone = (self.phone or "").strip() or None
        self.fee_override_reason = (self.fee_override_reason or "").strip() or None
        self.notes = (self.notes or "").strip() or None
        if len(self.full_name) < 2:
            raise ValueError("Please enter the walk-in guest's name")
        return self


class SessionParticipantResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    participant_kind: str
    source: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    fee_amount_kobo: int
    rate_code: Optional[str] = None
    payment_status: str
    waiver_status: str
    attendance_recorded: bool = False
