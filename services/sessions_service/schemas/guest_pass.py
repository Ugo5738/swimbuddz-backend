import uuid
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class GuestPassOffer(BaseModel):
    session_id: uuid.UUID
    title: str
    location_name: Optional[str] = None
    starts_at: datetime
    ends_at: datetime
    currency: str = "NGN"
    guest_fee_kobo: int
    community_dropin_fee_kobo: Optional[int] = None
    allows_guests: bool
    spaces_remaining: int


class GuestPassCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=160)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=32)
    date_of_birth: Optional[date] = None
    guardian_name: Optional[str] = Field(default=None, max_length=160)
    guardian_phone: Optional[str] = Field(default=None, max_length=32)
    waiver_accepted: bool
    marketing_consent: bool = False
    referral_code: Optional[str] = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def safeguarding(self):
        if not self.waiver_accepted:
            raise ValueError("The guest participation waiver must be accepted")
        today = date.today()
        if self.date_of_birth:
            age = (
                today.year
                - self.date_of_birth.year
                - (
                    (today.month, today.day)
                    < (self.date_of_birth.month, self.date_of_birth.day)
                )
            )
            if age < 18 and (not self.guardian_name or not self.guardian_phone):
                raise ValueError(
                    "Guardian name and phone are required for guests under 18"
                )
        return self


class GuestPassPublicResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    price_kobo: int
    additional_charges: list[dict]
    total_kobo: int
    payment_reference: str
    status: str
    reservation_expires_at: Optional[datetime] = None
    checkout_url: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GuestPassAdminResponse(GuestPassPublicResponse):
    full_name: str
    email: EmailStr
    phone: str
    referral_code: Optional[str] = None
    referrer_auth_id: Optional[str] = None
    referral_reward_bubbles: int
    referral_reward_status: str
    marketing_consent: bool
    attended_at: Optional[datetime] = None
    actual_swim_minutes: Optional[int] = None
    assessment_result: Optional[dict[str, Any]] = None
    converted_member_id: Optional[uuid.UUID] = None


class GuestPassConfirm(BaseModel):
    payment_reference: str


class GuestPassAttendanceUpdate(BaseModel):
    actual_swim_minutes: int = Field(..., ge=0, le=1440)
    assessment_result: Optional[dict[str, Any]] = None
    send_assessment_email: bool = True
